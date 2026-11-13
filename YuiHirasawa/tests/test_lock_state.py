"""Locker lifetime, shell gating, and initial lock-state discovery."""

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from test_locker_password import load_locker

ROOT = Path(__file__).resolve().parents[1]


def load_presence():
    repository = ModuleType('gi.repository')
    repository.Gio = Mock()
    spec = importlib.util.spec_from_file_location('lock_state_under_test', ROOT / 'lock_state.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gi': ModuleType('gi'), 'gi.repository': repository}):
        spec.loader.exec_module(module)
    return module


class LockPresenceTests(unittest.TestCase):
    def test_locker_owns_watched_name_and_releases_it_once(self):
        module = load_presence()
        presence = module.LockPresence()
        changed = Mock()
        monitor = module.LockStateMonitor(changed)
        gio = module.Gio
        self.assertEqual(gio.bus_own_name.call_args.args[1], module.LOCK_NAME)
        self.assertEqual(gio.bus_watch_name.call_args.args[1], module.LOCK_NAME)
        # Initial resolution also covers a shell starting while already locked.
        monitor._appeared(None, module.LOCK_NAME, ':1.2')
        changed.assert_called_with(True)
        monitor._vanished(None, module.LOCK_NAME)
        changed.assert_called_with(False)
        presence.close()
        presence.close()
        monitor.close()
        monitor.close()
        gio.bus_unown_name.assert_called_once_with(gio.bus_own_name.return_value)
        gio.bus_unwatch_name.assert_called_once_with(gio.bus_watch_name.return_value)

    def test_locker_publishes_before_showing_and_releases_on_exit_or_error(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                module = load_locker()
                events = []
                state = ModuleType('lock_state')
                presence = Mock()
                presence.close.side_effect = lambda: events.append('unlocked')
                state.LockPresence = Mock(side_effect=lambda: (events.append('locked'), presence)[1])
                module.PamAuthenticator = Mock()
                window = Mock()
                window.show_all.side_effect = lambda: events.append('shown')
                module.LockScreen = Mock(return_value=window)
                def main_loop():
                    events.append('loop')
                    if failed:
                        raise RuntimeError('test loop failure')
                module.Gtk.main = main_loop
                with patch.dict(sys.modules, {'lock_state': state}):
                    if failed:
                        with self.assertRaises(RuntimeError):
                            module.main()
                    else:
                        self.assertEqual(module.main(), 0)
                self.assertEqual(events, ['locked', 'shown', 'loop', 'unlocked'])


class StatusLockTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'control_center.py').read_text())
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'StatusControls')
        self.glib = Mock()
        self.namespace = dict(GLib=self.glib, open_animated_panel=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'control_center.py', 'exec'), self.namespace)
        self.controls = self.namespace['StatusControls'].__new__(self.namespace['StatusControls'])
        self.controls.locked = False
        self.controls.buttons = {'pwr': Mock(), 'audio': Mock()}
        self.controls.dialog = Mock()
        self.controls.delays = {'volume': 9}
        self.controls.pending = {'wifi': Mock()}
        self.controls._syncing = False
        self.controls.parent = Mock()
        self.controls.refresh = Mock()
        self.controls.bottom_right = True

    def test_lock_closes_open_panel_keeps_status_visible_and_cancels_changes(self):
        controls = self.controls
        controls._set_locked(True)
        self.assertTrue(controls.locked)
        controls.dialog.destroy.assert_called_once()
        controls.parent.hide.assert_not_called()
        for button in controls.buttons.values():
            button.set_sensitive.assert_called_with(False)
            button.hide.assert_not_called()
        self.glib.source_remove.assert_called_once_with(9)
        self.assertEqual(controls.pending, {})
        self.assertEqual(controls.delays, {})
        controls.show()
        self.namespace['open_animated_panel'].assert_not_called()
        controls.change('wifi', Mock())
        self.assertEqual(controls.pending, {})
        controls.refresh.assert_not_called()

    def test_unlock_restores_buttons_and_control_center_access(self):
        controls = self.controls
        controls._set_locked(True)
        controls._set_locked(False)
        for button in controls.buttons.values():
            button.set_sensitive.assert_called_with(True)
        controls.show()
        self.namespace['open_animated_panel'].assert_called_once_with(controls.dialog)


class DesktopTaskbarLockTests(unittest.TestCase):
    def test_desktop_taskbar_hides_and_stays_hidden_across_window_refreshes(self):
        class Window:
            def __init__(self, **_kwargs):
                self.calls = Mock()

            def __getattr__(self, name):
                return getattr(self.calls, name)

        tree = ast.parse((ROOT / 'desktop.py').read_text())
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'Taskbar')
        layers = Mock()
        namespace = dict(Gtk=Mock(Window=Window), GtkLayerShell=layers, GLib=Mock(),
                         ApplicationCatalog=type('Catalog', (), {}), WindowInfo=object,
                         WaylandWindowCollector=Mock(), ApplicationLauncher=Mock(),
                         LauncherControl=Mock(), SystemTray=Mock(), StatusControls=Mock(),
                         LockStateMonitor=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'desktop.py', 'exec'), namespace)
        bar = namespace['Taskbar']()
        self.assertTrue(bar._screen_locked)
        bar.set_no_show_all.assert_called_with(True)
        namespace['LockStateMonitor'].assert_called_once_with(bar._set_screen_locked)
        bar._set_screen_locked(False)
        bar.show_all.assert_called_once()
        bar.show_all.reset_mock()
        bar._set_screen_locked(True)
        bar.hide.assert_called_once()
        bar._launcher.hide.assert_called_once()
        bar.set_no_show_all.assert_called_with(True)
        layers.set_exclusive_zone.assert_called_with(bar, 0)
        # Window polling used to show the taskbar every 100ms.
        for _ in range(3):
            bar._set_groups({})
        bar.show_all.assert_not_called()
        bar._set_screen_locked(False)
        bar.show_all.assert_called_once()
        bar.set_no_show_all.assert_called_with(False)
        layers.set_exclusive_zone.assert_called_with(bar, bar.HEIGHT)


if __name__ == '__main__':
    unittest.main()
