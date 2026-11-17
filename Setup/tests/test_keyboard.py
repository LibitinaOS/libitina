"""Check the keyboard test easter egg without a display."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class KeyboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gi = ModuleType('gi')
        gi.require_version = Mock()
        repository = ModuleType('gi.repository')
        repository.Gtk = SimpleNamespace(
            Window=object, Entry=Mock(), MessageDialog=Mock(),
            DialogFlags=SimpleNamespace(MODAL=1),
            MessageType=SimpleNamespace(INFO=1),
            ButtonsType=SimpleNamespace(OK=1))
        repository.GLib = Mock()
        repository.Gdk = Mock()
        repository.GdkPixbuf = Mock()
        setup_dir = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('setup_keyboard_test_module', setup_dir / 'main.py')
        cls.setup = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
                patch.object(sys, 'path', [str(setup_dir), *sys.path]):
            spec.loader.exec_module(cls.setup)

    def setUp(self):
        self.wizard = self.setup.SetupWizard.__new__(self.setup.SetupWizard)
        self.wizard.config = {'keyboard': 'us'}
        self.wizard.add_heading = Mock()
        self.wizard.add_field = Mock()
        self.wizard.create_combo = Mock()
        self.setup.Gtk.Entry.reset_mock()
        self.setup.Gtk.MessageDialog.reset_mock()
        self.wizard.page_keyboard()

    def type_text(self, text):
        entry = Mock(get_text=Mock(return_value=text))
        self.wizard.on_keyboard_test_changed(entry)

    def test_keyboard_test_connects_changed_handler(self):
        self.wizard.keyboard_test.connect.assert_called_once_with(
            'changed', self.wizard.on_keyboard_test_changed)

    def test_phrase_ignores_case_and_comma(self):
        for phrase in ('Every day, I imagine a future where I can be with you',
                       'every day i imagine a future where i can be with you',
                       'EVERY DAY, I IMAGINE A FUTURE WHERE I CAN BE WITH YOU'):
            with self.subTest(phrase=phrase):
                self.type_text('')
                self.setup.Gtk.MessageDialog.reset_mock()
                self.type_text(phrase)
                self.setup.Gtk.MessageDialog.assert_called_once_with(
                    transient_for=self.wizard, flags=self.setup.Gtk.DialogFlags.MODAL,
                    message_type=self.setup.Gtk.MessageType.INFO,
                    buttons=self.setup.Gtk.ButtonsType.OK, text='Just Monika')
                dialog = self.setup.Gtk.MessageDialog.return_value
                dialog.run.assert_called_once()
                dialog.destroy.assert_called_once()

    def test_other_text_does_not_open_dialog(self):
        for text in ('', 'Every day, I imagine a future',
                     'Every day, I imagine a future where I can be with someone'):
            self.type_text(text)
        self.setup.Gtk.MessageDialog.assert_not_called()

    def test_dialog_does_not_repeat_until_phrase_is_reentered(self):
        phrase = 'Every day, I imagine a future where I can be with you'
        self.type_text(phrase)
        self.type_text(phrase.upper())
        self.assertEqual(self.setup.Gtk.MessageDialog.call_count, 1)
        self.type_text('')
        self.type_text(phrase)
        self.assertEqual(self.setup.Gtk.MessageDialog.call_count, 2)


if __name__ == '__main__':
    unittest.main()
