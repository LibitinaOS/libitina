#!/usr/bin/env python3
"""Register the YuiHirasawa login session without changing other sessions."""

import argparse
from pathlib import Path
import re
import shlex


def quote_exec(value):
    # Desktop Entry Exec quoting, followed by key-file string escaping.
    if any(char in value for char in ('\n', '\r', '\x00')):
        raise ValueError("Session paths must not contain control characters")
    escaped = ''.join('\\' + char if char in '\\"`$' else char for char in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


def desktop_entry(wrapper=Path('/usr/local/bin/yuihirasawa-session')):
    # Debian Xsession does `exec $STARTUP`, not shell evaluation. Quotes around
    # individual arguments survive as filename characters. Use one safe path.
    if not wrapper.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_./+-]+', str(wrapper)):
        raise ValueError('Session wrapper path must be absolute with no spaces or shell metacharacters')
    return (
        "[Desktop Entry]\n"
        "Name=YuiHirasawa\n"
        "Comment=Yui shell on Labwc\n"
        f"Exec={wrapper}\n"
        "TryExec=labwc\n"
        "Type=Application\n"
        "X-LightDM-Session-Type=wayland\n"
        "DesktopNames=labwc;\n"
    )


def install(session_dir, bin_dir):
    wrapper = bin_dir / 'yuihirasawa-session'
    entry = desktop_entry(wrapper)
    launcher = Path(__file__).resolve().with_name('launcher.py')
    session_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(launcher))
                       + ' --session\n', encoding='utf-8')
    wrapper.chmod(0o755)
    target = session_dir / 'yuihirasawa.desktop'
    target.write_text(entry, encoding='utf-8')
    target.chmod(0o644)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, default=Path("/usr/share/wayland-sessions"),
                        help="login manager's Wayland session directory")
    parser.add_argument('--bin-dir', type=Path, default=Path('/usr/local/bin'),
                        help='system session wrapper directory (absolute, no spaces)')
    args = parser.parse_args()
    target = install(args.session_dir, args.bin_dir)
    print(f"Installed {target}. Select YuiHirasawa at login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
