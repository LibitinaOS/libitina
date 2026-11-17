"""Check application payloads, system destinations, and per-user compatibility."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


APPLICATIONS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('application_installer_test', APPLICATIONS / 'install.py')
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_system_payload_desktop_entry_shared_files_and_update(self):
        application = installer.Application(
            'YuiSettings', APPLICATIONS / 'Production/Yui/YuiSettings')
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / 'usr/share'
            payload, desktop = installer.install(application, data, system=True)
            self.assertEqual(payload, data / 'libitina/applications/YuiSettings')
            self.assertEqual(desktop, data / 'applications/libitina-YuiSettings.desktop')
            self.assertEqual(payload.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(desktop.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(desktop.stat().st_mode & 0o777, 0o644)
            self.assertTrue((payload / 'launch.sh').stat().st_mode & 0o111)
            contents = desktop.read_text()
            self.assertIn(f'Exec="{payload}/launch.sh"\n', contents)
            self.assertIn(f'Path={payload}\n', contents)
            for name in ('displays.py', 'settings.py'):
                self.assertEqual((payload / name).read_bytes(),
                                 (APPLICATIONS.parent / 'YuiHirasawa' / name).read_bytes())
            (payload / 'stale').touch()
            installer.install(application, data, system=True)
            self.assertFalse((payload / 'stale').exists())
            self.assertEqual(list(desktop.parent.glob('libitina-*.desktop')), [desktop])

    def test_user_install_keeps_private_parent_directories(self):
        application = installer.Application(
            'YuiSettings', APPLICATIONS / 'Production/Yui/YuiSettings')
        with tempfile.TemporaryDirectory() as temporary:
            payload, desktop = installer.install(application, Path(temporary))
            self.assertEqual(payload.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(desktop.parent.stat().st_mode & 0o777, 0o700)

    def test_system_main_ignores_user_data_directory(self):
        application = installer.Application('Demo', APPLICATIONS / 'Production/Global/Demo')
        with patch.object(installer.os, 'geteuid', return_value=0), \
                patch.object(installer, 'data_home') as data_home, \
                patch.object(installer, 'find_applications', return_value=([application], [])), \
                patch.object(installer, 'install') as install, \
                patch.object(installer, 'refresh_applications') as refresh:
            self.assertEqual(installer.main(['--system']), 0)
        data_home.assert_not_called()
        install.assert_called_once_with(application, Path('/usr/share'), system=True)
        refresh.assert_called_once_with(Path('/usr/share/applications'))

    def test_user_main_retains_data_home_and_filters(self):
        application = installer.Application('Demo', APPLICATIONS / 'Production/Global/Demo')
        with patch.object(installer, 'data_home', return_value=Path('/user/share')), \
                patch.object(installer, 'find_applications', return_value=([application], [])) as find, \
                patch.object(installer, 'install') as install, \
                patch.object(installer, 'refresh_applications'):
            self.assertEqual(installer.main(['--kind', 'Production', '--environment', 'Global']), 0)
        find.assert_called_once_with(APPLICATIONS, {'Production'}, {'Global'})
        install.assert_called_once_with(application, Path('/user/share'), system=False)

    def test_system_requires_root_but_dry_run_does_not(self):
        application = installer.Application('Demo', APPLICATIONS / 'Production/Global/Demo')
        with patch.object(installer.os, 'geteuid', return_value=1000), \
                patch.object(installer, 'find_applications', return_value=([application], [])), \
                patch.object(installer, 'install') as install, \
                patch.object(installer, 'refresh_applications') as refresh:
            self.assertEqual(installer.main(['--system']), 1)
            self.assertEqual(installer.main(['--system', '--dry-run']), 0)
        install.assert_not_called()
        refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
