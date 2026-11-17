"""Validate unprivileged wizard exports before executing privileged commands."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('provision', Path(__file__).parents[1] / 'provision.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ValidationTests(unittest.TestCase):
    def config(self):
        return dict(username='alice', password=' päss日本語 123 ', fullname='Alice',
                    hostname='libitina', language='en_US', keyboard='us', timezone='UTC')

    def test_valid_preserves_password(self):
        data = self.config()
        self.assertEqual(provision.validate(data)['password'], ' päss日本語 123 ')

    def test_generated_hostname(self):
        data = self.config()
        data['hostname'] = 'Libitina-A7X2Q9'
        self.assertEqual(provision.validate(data)['hostname'], 'Libitina-A7X2Q9')

    def test_appearance_settings(self):
        for mode, theme, prefer_dark, color_scheme in (
                ('light', 'DeletescapeUi', 'false', 'default'),
                ('dark', 'DeletescapeUi-Dark', 'true', 'prefer-dark')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as home, \
                    patch.object(provision.os, 'chown'), patch.object(provision, 'run') as run:
                account = SimpleNamespace(pw_dir=home, pw_name='alice', pw_uid=1000, pw_gid=1000)
                provision.apply_appearance(account, mode)
                settings = (Path(home) / '.config/gtk-3.0/settings.ini').read_text()
                self.assertIn(f'gtk-theme-name={theme}\n', settings)
                self.assertIn(f'gtk-application-prefer-dark-theme={prefer_dark}\n', settings)
                values = [call.args[-2:] for call in run.call_args_list]
                self.assertEqual(values, [('gtk-theme', theme), ('color-scheme', color_scheme)])

    def test_invalid_exports(self):
        for field, values in {
            'username': ['root', 'defaultuser0', '-oops', 'a' * 33],
            'password': ['short', 'password\nroot:injected'],
            'fullname': ['Alice:0:0', 'Alice\nInjected'],
            'hostname': ['-host', 'host\ninjected'],
            'appearance': ['bogus', 'dark\ninjected'],
            'language': ['en_US\nInjected', 'bogus'],
            'keyboard': ['us"\nInjected', 'bogus'],
            'timezone': ['/etc/passwd', '../passwd', 'Nonexistent/Zone'],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    data = self.config()
                    data[field] = value
                    with self.assertRaises(ValueError):
                        provision.validate(data)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.marker = self.root / 'in-progress'
        self.files = tuple(self.root / name for name in ('autologin', 'sudoers', 'session'))
        for path in self.files:
            path.write_text(path.name)
            path.chmod(0o440 if path.name == 'sudoers' else 0o644)
        for name, value in (('STATE', self.state), ('IN_PROGRESS', self.marker),
                            ('OOBE_FILES', self.files)):
            patcher = patch.object(provision, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ('geteuid', 'sync'):
            patcher = patch.object(provision.os, target, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_interruption_before_first_system_change_leaves_marker(self):
        with patch.object(provision, 'run', side_effect=RuntimeError('interrupted')) as run:
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                provision.main()
        self.assertTrue(self.marker.exists())
        run.assert_called_once_with('systemctl', 'stop', 'lightdm.service')

    def test_boot_restores_deleted_oobe_account_and_files(self):
        provision.mark_provisioning_started()
        for path in self.files:
            path.unlink()
        with patch.object(provision.pwd, 'getpwnam', side_effect=KeyError), \
                patch.object(provision, 'run') as run:
            provision.recover_oobe()
        run.assert_called_once_with('useradd', '--create-home', '--shell', '/bin/bash', 'defaultuser0')
        for path in self.files:
            self.assertEqual(path.read_text(), path.name)
        self.assertEqual(self.files[1].stat().st_mode & 0o777, 0o440)
        self.assertTrue(self.marker.exists())

    def test_normal_and_completed_boots_do_not_restore_oobe(self):
        for complete in (False, True):
            if complete:
                provision.mark_provisioning_started()
                (self.state / 'setup-complete').touch()
            with patch.object(provision.pwd, 'getpwnam') as lookup, \
                    patch.object(provision, 'run') as run:
                provision.recover_oobe()
            lookup.assert_not_called()
            run.assert_not_called()

    def test_success_removes_marker_only_after_completion(self):
        export = self.root / 'setup.json'
        export.write_text('{}')
        account = SimpleNamespace(pw_uid=export.stat().st_uid, pw_gid=export.stat().st_gid,
                                  pw_dir=str(self.root), pw_name='alice')
        # Simulate a retry of the account created by the interrupted attempt.
        self.state.mkdir()
        (self.state / 'setup-user').write_text('alice')
        data = ValidationTests().config()

        def redirect_path(value):
            return self.root / 'locale.gen' if value == '/etc/locale.gen' else Path(value)

        def run(*args, **kwargs):
            if args == ('systemctl', 'reboot'):
                self.assertTrue((self.state / 'setup-complete').exists())
                self.assertFalse(self.marker.exists())
            else:
                self.assertTrue(self.marker.exists())

        with patch.object(provision, 'EXPORT', export), \
                patch.object(provision, 'Path', side_effect=redirect_path), \
                patch.object(provision.pwd, 'getpwnam', return_value=account), \
                patch.object(provision, 'validate', return_value=data), \
                patch.object(provision.os, 'chown'), \
                patch.object(provision, 'apply_appearance'), \
                patch.object(provision.subprocess, 'run'), \
                patch.object(provision, 'run', side_effect=run):
            provision.main()
        self.assertTrue((self.state / 'setup-complete').exists())
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
