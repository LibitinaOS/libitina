"""Exercise the installed entry through Debian Xsession's word splitting."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_session', SOURCE / 'install-session.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class EntryTests(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/python3').exists(), 'session requires system Python')
    def test_xsession_executes_wrapper_with_quoted_checkout_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "checkout with spaces and 'quotes'"
            source.mkdir()
            (source / 'launcher.py').write_text('import json, sys; print(json.dumps(sys.argv[1:]))\n')
            with patch.object(installer, '__file__', str(source / 'install-session.py')):
                target = installer.install(base / 'sessions', base / 'bin')
            entry = target.read_text()
            self.assertIn('X-LightDM-Session-Type=wayland', entry)
            command = next(line.removeprefix('Exec=') for line in entry.splitlines()
                           if line.startswith('Exec='))
            # Mirrors /etc/X11/Xsession.d/99x11-common_start; no eval/reparsing.
            completed = subprocess.run(['sh', '-c', 'exec $STARTUP'],
                                       env={**os.environ, 'STARTUP': command},
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), ['--session'])
            self.assertEqual(Path(command).stat().st_mode & 0o777, 0o755)

    def test_unsafe_wrapper_location_is_rejected(self):
        for path in ('relative', '/a path/bin/session', '/bin/$session'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                installer.desktop_entry(Path(path))


if __name__ == '__main__':
    unittest.main()
