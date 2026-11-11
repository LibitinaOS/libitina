"""Check setup password persistence without GTK or a real account change."""

import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class PasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gi = ModuleType('gi')
        gi.require_version = Mock()
        repository = ModuleType('gi.repository')
        repository.Gtk = SimpleNamespace(
            Window=object, Entry=Mock(), MessageDialog=Mock(), main_quit=Mock(),
            DialogFlags=SimpleNamespace(MODAL=1),
            MessageType=SimpleNamespace(INFO=1),
            ButtonsType=SimpleNamespace(OK=1),
            InputPurpose=SimpleNamespace(PASSWORD=1))
        repository.GLib = Mock()
        repository.GdkPixbuf = Mock()
        setup_dir = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('setup_password_test_module', setup_dir / 'main.py')
        cls.setup = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
                patch.object(sys, 'path', [str(setup_dir), *sys.path]):
            spec.loader.exec_module(cls.setup)

    def wizard(self, source='manual', password=' päss日本語 123 '):
        wizard = self.setup.SetupWizard.__new__(self.setup.SetupWizard)
        wizard.config = dict(account_source=source, fullname='Test User', username='test', password='')
        wizard.current_page = 5
        wizard.fullname_entry = Mock(get_text=Mock(return_value='Test User'))
        wizard.username_entry = Mock(get_text=Mock(return_value='test'))
        wizard.password_entry = Mock(get_text=Mock(return_value=password))
        wizard.confirm_entry = Mock(get_text=Mock(return_value=password))
        wizard.show_error = Mock()
        return wizard

    def test_validated_password_is_exported_verbatim_with_private_permissions(self):
        for source in ('manual', 'github'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as home:
                wizard = self.wizard(source)
                self.assertTrue(wizard.save_current_page())
                with patch.object(self.setup.Path, 'home', return_value=Path(home)):
                    wizard.finish_setup()
                output = Path(home) / '.config' / 'os-setup' / 'setup.json'
                self.assertEqual(json.loads(output.read_text())['password'], ' päss日本語 123 ')
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                wizard.show_error.assert_not_called()

    def test_invalid_password_does_not_replace_saved_password(self):
        for password, confirmation in (('short', 'short'), ('long enough', 'different')):
            with self.subTest(password=password):
                wizard = self.wizard(password=password)
                wizard.config['password'] = 'previous password'
                wizard.confirm_entry.get_text.return_value = confirmation
                self.assertFalse(wizard.save_current_page())
                self.assertEqual(wizard.config['password'], 'previous password')

    def test_revisiting_account_restores_both_password_fields(self):
        wizard = self.wizard()
        wizard.config['password'] = 'saved password'
        wizard.add_heading = Mock()
        wizard.add_field = Mock()
        with patch.object(self.setup.Gtk, 'Entry') as entry:
            wizard.page_user()
        self.assertEqual([call.kwargs for call in entry.call_args_list[-2:]],
                         [{'text': 'saved password'}, {'text': 'saved password'}])


if __name__ == '__main__':
    unittest.main()
