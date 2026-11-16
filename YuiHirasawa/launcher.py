#!/usr/bin/env python3
"""Supervise the Yui shell and notification daemon for a Wayland session."""

import argparse
import fcntl
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

from settings import load_config

ROOT = Path(__file__).resolve().parent
RESTART_DELAY = 1.0


def journal_stream(priority):
    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        stream.connect('/run/systemd/journal/stdout')
        # journald's stdout stream protocol: identifier, unit, priority,
        # level-prefix, forward-to-syslog, forward-to-kmsg, forward-to-console.
        stream.sendall(f'yui\n\n{priority}\n0\n0\n0\n0\n'.encode())
        return stream
    except OSError:
        stream.close()
        raise


def connect_journal():
    """Attach streams without replacing the session with a fallible logger."""
    if os.environ.get('YUI_JOURNAL_CONNECTED') == '1':
        return
    streams, saved = [], []
    try:
        # Connect both before changing either descriptor. Logging is optional.
        streams.append(journal_stream(6))
        streams.append(journal_stream(3))
        sys.stdout.flush()
        sys.stderr.flush()
        saved.append(os.dup(1))
        saved.append(os.dup(2))
        os.dup2(streams[0].fileno(), 1)
        os.dup2(streams[1].fileno(), 2)
    except OSError as exc:
        for descriptor, original in zip((1, 2), saved):
            os.dup2(original, descriptor)
        print(f'Yui: journal unavailable ({exc}); keeping stdout/stderr',
              file=sys.stderr, flush=True)
    else:
        os.environ['YUI_JOURNAL_CONNECTED'] = '1'
        os.environ['PYTHONUNBUFFERED'] = '1'
    finally:
        for stream in streams:
            stream.close()
        for descriptor in saved:
            os.close(descriptor)


def prepare_clients():
    # Pi OS and X11 greeters can export GDK_BACKEND=x11. Layer-shell clients
    # must connect to the compositor which launched this supervisor.
    if os.environ.get('WAYLAND_DISPLAY'):
        os.environ['GDK_BACKEND'] = 'wayland'
        os.environ['XDG_SESSION_TYPE'] = 'wayland'
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        command = ['dbus-run-session', '--', sys.executable,
                   str(Path(__file__).resolve()), *sys.argv[1:]]
        os.execvp(command[0], command)


def terminate_group(child, sig=signal.SIGKILL):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def supervise(commands):
    """Restart exited children, and reap every child when the session stops."""
    stopping = False
    children = {}
    retry_at = {}
    from lille import protection_triggered

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        while not stopping:
            for name, command in commands.items():
                child = children.get(name)
                if name == "squeekboard" and protection_triggered():
                    if child is not None and child.poll() is None:
                        child.terminate()
                    continue
                if child is not None:
                    status = child.poll()
                    if status is None:
                        continue
                    print(f"Yui: {name} exited ({status}); restarting", file=sys.stderr, flush=True)
                    if name == "launcher":
                        terminate_group(child)
                    del children[name]
                    retry_at[name] = time.monotonic() + RESTART_DELAY
                if time.monotonic() >= retry_at.get(name, 0):
                    try:
                        children[name] = subprocess.Popen(command, cwd=ROOT, start_new_session=(name == "launcher"))
                        print(f"Yui: started {name} (PID {children[name].pid})", file=sys.stderr, flush=True)
                    except OSError as exc:
                        print(f"Yui: could not start {name}: {exc}", file=sys.stderr, flush=True)
                        retry_at[name] = time.monotonic() + RESTART_DELAY
            time.sleep(0.1)
    finally:
        for name, child in children.items():
            if name == "launcher":
                terminate_group(child, signal.SIGTERM)
            elif child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 7
        for child in children.values():
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                if "launcher" in commands:
                    terminate_group(child)
                else:
                    child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="store_true", help="start the Labwc login session")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    connect_journal()
    # Shared by all workers, retained across their restarts for this session.
    os.environ.setdefault("YUI_LILLE_SESSION", uuid.uuid4().hex)
    if args.session:
        from labwc_session import main as run_session
        return run_session()
    prepare_clients()
    if args.worker:
        commands = {name: [sys.executable, str(ROOT / name)]
                    for name in ("notification.py", "main.py", "displays.py")}
        if load_config()["squeekboard_enabled"]:
            commands["squeekboard"] = ["squeekboard"]
        return supervise(commands)

    # Hold the lock in the supervisor so a restarted worker cannot race autostart.
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/yui-{os.getuid()}")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / "yui-launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return supervise({"launcher": [sys.executable, str(Path(__file__).resolve()), "--worker"]})


if __name__ == "__main__":
    raise SystemExit(main())
