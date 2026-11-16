"""Verify journal wrapping preserves launcher arguments and process ownership."""

import os
import json
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher


class JournalTests(unittest.TestCase):
    def test_worker_starts_keyboard_only_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), \
                    patch.object(sys, "argv", ["launcher.py", "--worker"]), \
                    patch.object(launcher, "connect_journal"), \
                    patch.object(launcher, "prepare_clients"), \
                    patch.object(launcher, "load_config", return_value={"squeekboard_enabled": enabled}), \
                    patch.object(launcher, "supervise", return_value=0) as supervise:
                self.assertEqual(launcher.main(), 0)
                commands = supervise.call_args.args[0]
                self.assertEqual("squeekboard" in commands, enabled)
                if enabled:
                    self.assertEqual(commands["squeekboard"], ["squeekboard"])
                self.assertIn("main.py", commands)

    def test_journal_stream_protocol(self):
        from unittest.mock import MagicMock
        stream = MagicMock()
        with patch.object(launcher.socket, 'socket', return_value=stream):
            self.assertIs(launcher.journal_stream(6), stream)
        stream.connect.assert_called_once_with('/run/systemd/journal/stdout')
        stream.sendall.assert_called_once_with(b'yui\n\n6\n0\n0\n0\n0\n')

    def test_attach_in_place_without_replacing_session(self):
        from unittest.mock import MagicMock
        streams = [MagicMock(), MagicMock()]
        for stream, fd in zip(streams, (30, 31)):
            stream.fileno.return_value = fd
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(launcher, 'journal_stream', side_effect=streams), \
                patch.object(launcher.os, 'dup', side_effect=[40, 41]), \
                patch.object(launcher.os, 'dup2') as redirect, \
                patch.object(launcher.os, 'close'), patch.object(launcher.os, 'execve') as execute:
            launcher.connect_journal()
            self.assertEqual([call.args for call in redirect.call_args_list], [(30, 1), (31, 2)])
            self.assertEqual(os.environ['YUI_JOURNAL_CONNECTED'], '1')
            execute.assert_not_called()
        for stream in streams:
            stream.close.assert_called_once()

    def test_descendants_do_not_reconnect(self):
        with patch.dict(os.environ, {'YUI_JOURNAL_CONNECTED': '1'}), \
                patch.object(launcher, 'journal_stream') as connect:
            launcher.connect_journal()
            connect.assert_not_called()

    def test_failed_journal_connection_does_not_redirect_or_abort(self):
        from unittest.mock import MagicMock
        for streams in ([OSError('denied')], [MagicMock(), OSError('denied')]):
            with self.subTest(streams=streams), patch.dict(os.environ, {}, clear=True), \
                    patch.object(launcher, 'journal_stream', side_effect=streams), \
                    patch.object(launcher.os, 'dup2') as redirect, \
                    patch.object(sys, 'stderr') as stderr:
                launcher.connect_journal()
                redirect.assert_not_called()
                self.assertTrue(stderr.write.called)
                self.assertNotIn('YUI_JOURNAL_CONNECTED', os.environ)

    def test_pi_clients_override_x11_backend(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0', 'GDK_BACKEND': 'x11',
                                     'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/session/bus'}, clear=True):
            launcher.prepare_clients()
            self.assertEqual(os.environ['GDK_BACKEND'], 'wayland')
            self.assertEqual(os.environ['XDG_SESSION_TYPE'], 'wayland')

    def test_real_child_starts_when_journal_connection_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / 'child.json'
            child = (
                'import json, os, signal; from pathlib import Path; '
                f'Path({str(result)!r}).write_text(json.dumps(dict(os.environ))); '
                'os.kill(os.getppid(), signal.SIGTERM)')
            script = (
                'import sys, os; '
                f'sys.path.insert(0, {str(Path(launcher.__file__).parent)!r}); '
                'import launcher; '
                'launcher.journal_stream = lambda priority: (_ for _ in ()).throw(PermissionError("denied")); '
                'os.environ.pop("YUI_JOURNAL_CONNECTED", None); '
                'os.environ.update(WAYLAND_DISPLAY="wayland-0", GDK_BACKEND="x11", '
                'DBUS_SESSION_BUS_ADDRESS="unix:path=/session/bus"); '
                'launcher.connect_journal(); launcher.prepare_clients(); '
                f'raise SystemExit(launcher.supervise({{"probe": [sys.executable, "-c", {child!r}]}}))')
            completed = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                       text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            environment = json.loads(result.read_text())
            self.assertEqual(environment['GDK_BACKEND'], 'wayland')
            self.assertEqual(environment['XDG_SESSION_TYPE'], 'wayland')
            self.assertIn('journal unavailable', completed.stderr)

    def test_missing_bus_reexecutes_under_session_bus(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(sys, 'argv', ['launcher.py', '--worker']), \
                patch.object(launcher.os, 'execvp') as execute:
            launcher.prepare_clients()
            self.assertEqual(execute.call_args.args[0], 'dbus-run-session')
            self.assertEqual(execute.call_args.args[1][-1], '--worker')


if __name__ == '__main__':
    unittest.main()
