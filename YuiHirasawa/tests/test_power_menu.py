"""Power menu behavior without executing system commands or opening GTK windows."""

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load_menu():
    class ActionError(Exception):
        @property
        def message(self):
            return str(self)

    repository = ModuleType('gi.repository')
    gtk = Mock()
    gtk.MenuItem.side_effect = lambda **kwargs: Mock(**kwargs)
    gtk.ResponseType.OK = 1
    gtk.ResponseType.CANCEL = 0
    repository.Gtk = gtk
    repository.GLib = SimpleNamespace(Error=ActionError)
    backend = ModuleType('backend')
    backend.run_session_action = Mock()
    widgets = ModuleType('shell_widgets')
    widgets.show_error_dialog = Mock()
    spec = importlib.util.spec_from_file_location('power_menu_under_test',
        Path(__file__).resolve().parents[1] / 'power_menu.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gi': ModuleType('gi'), 'gi.repository': repository,
                                 'backend': backend, 'shell_widgets': widgets}):
        spec.loader.exec_module(module)
    return module, gtk, ActionError


class PowerMenuTests(unittest.TestCase):
    def setUp(self):
        self.module, self.gtk, self.error = load_menu()
        self.parent = Mock()
        self.success = Mock()
        self.menu = self.module.create_power_menu(self.parent, self.success)
        self.items = {call.args[0].label: call.args[0]
                      for call in self.menu.append.call_args_list
                      if call.args[0] is not self.gtk.SeparatorMenuItem.return_value}

    def activate(self, label):
        item = self.items[label]
        _signal, callback, *args = item.connect.call_args.args
        callback(item, *args)

    def test_control_center_wires_power_menu_to_its_window_and_dismissal(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'control_center.py').read_text())
        controls = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'StatusControls')
        menu_factory = Mock(return_value=self.menu)
        namespace = {'Gtk': self.gtk, 'create_power_menu': menu_factory,
                     '_prepare_launcher_dialog': Mock()}
        exec(compile(ast.Module(body=[controls], type_ignores=[]),
                     'control_center.py', 'exec'), namespace)
        panel = namespace['StatusControls'].__new__(namespace['StatusControls'])
        panel.parent = Mock()
        panel.parent.get_window.return_value = None
        panel.bottom_right = False
        panel.data = {}
        panel._create_dialog()
        menu_factory.assert_called_once_with(panel.dialog, panel._dismiss_dialog)
        self.gtk.MenuButton.assert_called_once_with(label='Power')
        self.gtk.MenuButton.return_value.set_popup.assert_called_once_with(self.menu)

    def test_all_launcher_actions_present(self):
        self.assertEqual(list(self.items), ['Lock', 'Suspend', 'Restart', 'Power Off', 'Kill Shell'])
        self.menu.show_all.assert_called_once()

    def test_lock_runs_without_confirmation(self):
        self.activate('Lock')
        self.gtk.MessageDialog.assert_not_called()
        self.module.run_session_action.assert_called_once_with(('loginctl', 'lock-session'))
        self.success.assert_called_once_with()

    def test_confirmed_actions_use_same_confirmation_and_commands(self):
        for label, command in [('Suspend', 'suspend'), ('Restart', 'reboot'), ('Power Off', 'poweroff')]:
            with self.subTest(label=label):
                self.gtk.MessageDialog.return_value.run.return_value = self.gtk.ResponseType.OK
                self.activate(label)
                self.module.run_session_action.assert_called_with(('systemctl', command))
                self.gtk.MessageDialog.assert_called_with(
                    transient_for=self.parent, modal=True,
                    message_type=self.gtk.MessageType.QUESTION,
                    buttons=self.gtk.ButtonsType.CANCEL, text=f'{label}?')
                self.gtk.MessageDialog.return_value.destroy.assert_called()
        self.assertEqual(self.success.call_count, 3)

    def test_cancelled_confirmation_does_not_run_or_close(self):
        self.gtk.MessageDialog.return_value.run.return_value = self.gtk.ResponseType.CANCEL
        self.activate('Power Off')
        self.module.run_session_action.assert_not_called()
        self.success.assert_not_called()

    def test_failure_is_displayed_without_closing_control_center(self):
        self.module.run_session_action.side_effect = self.error('Permission denied')
        self.activate('Lock')
        self.module.show_error_dialog.assert_called_once_with(
            self.parent, 'Could not lock', 'Permission denied')
        self.success.assert_not_called()

    def test_launcher_error_callback_is_preserved(self):
        error = Mock()
        menu = self.module.create_power_menu(self.parent, self.success, error)
        lock = menu.append.call_args_list[-6].args[0]
        self.module.run_session_action.side_effect = self.error('Failed')
        _signal, callback, *args = lock.connect.call_args.args
        callback(lock, *args)
        error.assert_called_once_with('Could not lock', 'Failed')
        self.module.show_error_dialog.assert_not_called()

    def test_kill_shell_quits_without_running_a_system_command(self):
        self.activate('Kill Shell')
        self.gtk.main_quit.assert_called_once_with()
        self.module.run_session_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
