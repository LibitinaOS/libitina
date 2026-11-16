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


if __name__ == '__main__':
    unittest.main()
