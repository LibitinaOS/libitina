"""Opt-in locker interaction checks (YUI_RUN_GTK_TESTS=1, GTK display required)."""

import importlib.util
import os
from pathlib import Path
import pwd
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings


@unittest.skipUnless(os.environ.get("YUI_RUN_GTK_TESTS") == "1", "requires a GTK display")
class LockScreenGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk, GLib
        Gtk.init([])
        cls.Gtk, cls.Gdk, cls.GLib = Gtk, Gdk, GLib
        require = gi.require_version
        import gi.repository
        with patch.object(gi, "require_version", side_effect=lambda name, version:
                          None if name == "GtkLayerShell" else require(name, version)), \
             patch.dict(gi.repository.__dict__, {"GtkLayerShell": Mock()}):
            spec = importlib.util.spec_from_file_location(
                "locker_gtk_under_test", Path(__file__).resolve().parents[1] / "locker.py")
            cls.module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.module
            spec.loader.exec_module(cls.module)
            import wallpaper
            cls.wallpaper = wallpaper

    def settle(self, milliseconds=300):
        loop = self.GLib.MainLoop()
        self.GLib.timeout_add(milliseconds, lambda: (loop.quit(), False)[1])
        loop.run()

    def screen(self, shell):
        config = {**settings.DEFAULT_CONFIG, "shell": shell}
        with patch.object(self.module, "load_config", return_value=config), \
             patch.object(self.wallpaper, "load_config", return_value=config), \
             patch.object(self.module.LockScreen, "_set_keyboard_visible", Mock()):
            window = self.module.LockScreen(Mock())
        window._set_keyboard_visible = Mock()
        self.addCleanup(window.destroy)
        window.set_default_size(360, 720)
        window.show_all()
        self.settle()
        return window

    def test_initial_clock_and_username_only_on_desktop(self):
        for shell in ("mobile", "desktop"):
            with self.subTest(shell=shell):
                window = self.screen(shell)
                self.assertEqual(window._page, 0)
                self.assertEqual(window._offset.get_value(), 0)
                self.assertFalse(window._password_page.get_sensitive())
                self.assertIsNot(window.get_focus(), window._entry)
                labels = [widget.get_text() for widget in
                          window._password_page.get_children()[0].get_children()
                          if isinstance(widget, self.Gtk.Label)]
                username = pwd.getpwuid(os.getuid()).pw_name
                self.assertEqual(username in labels, shell == "desktop")
                self.assertTrue(window._clock_label.get_text())
                self.assertTrue(window._date_label.get_text())

    def test_upward_drag_tracks_finger_then_snaps_and_back_clears_secret(self):
        window = self.screen("mobile")
        height = window._page_height
        gesture = Mock()
        window._on_drag_begin(gesture, 100, 600)
        window._on_drag_update(gesture, 2, -height * 0.3)
        self.assertAlmostEqual(window._offset.get_value(), height * 0.3)
        gesture.set_state.assert_called_with(self.Gtk.EventSequenceState.CLAIMED)
        self.assertFalse(window._password_page.get_sensitive())
        window._on_drag_end(gesture, 2, -height * 0.3)
        self.settle()
        self.assertEqual(window._page, 1)
        self.assertEqual(window._offset.get_value(), height)
        self.assertIs(window.get_focus(), window._entry)
        window._entry.set_text("secret")
        window._show_clock()
        self.assertEqual(window._entry.get_text(), "")
        self.settle()
        self.assertEqual(window._offset.get_value(), 0)
        self.assertIsNot(window.get_focus(), window._entry)

    def test_short_horizontal_cancelled_and_downward_drags_keep_clock(self):
        window = self.screen("mobile")
        for dx, dy in ((0, -25), (200, -20), (0, 200), (0, 0)):
            window._on_drag_begin(Mock(), 100, 400)
            window._on_drag_update(Mock(), dx, dy)
            window._on_drag_end(Mock(), dx, dy)
            self.settle()
            self.assertEqual(window._page, 0)
            self.assertEqual(window._offset.get_value(), 0)
        window._on_drag_begin(Mock(), 100, 400)
        window._on_drag_update(Mock(), 0, -300)
        window._on_drag_cancel()
        self.settle()
        self.assertEqual(window._offset.get_value(), 0)

    def test_desktop_click_opens_but_mobile_tap_does_not(self):
        for shell, expected in (("desktop", 1), ("mobile", 0)):
            window = self.screen(shell)
            window._on_drag_begin(Mock(), 100, 400)
            window._on_drag_end(Mock(), 0, 0)
            self.settle()
            self.assertEqual(window._page, expected)

    def test_downward_drag_returns_to_clock(self):
        window = self.screen("mobile")
        window._set_page(1, animate=False)
        window._entry.set_text("secret")
        height = window._page_height
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_update(Mock(), 0, height * 0.3)
        self.assertAlmostEqual(window._offset.get_value(), height * 0.7)
        window._on_drag_end(Mock(), 0, height * 0.3)
        self.settle()
        self.assertEqual(window._page, 0)
        self.assertEqual(window._entry.get_text(), "")

    def test_resize_preserves_page_and_authentication_blocks_navigation(self):
        window = self.screen("desktop")
        window._set_page(1, animate=False)
        window.resize(640, 400)
        self.settle()
        self.assertEqual(window._offset.get_value(), window._page_height)
        window._authenticating = True
        window._show_clock()
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_update(Mock(), 0, 300)
        window._on_drag_end(Mock(), 0, 300)
        self.assertEqual(window._page, 1)
        window._authenticating = False
        window._authentication_finished(self.module.AuthenticationResult(False, "Incorrect password"))
        self.assertEqual(window._page, 1)
        self.assertEqual(window._status.get_text(), "Incorrect password")
        self.assertIs(window.get_focus(), window._entry)

    def assert_page_geometry(self, window):
        height = window._pages.get_allocated_height()
        self.assertEqual(window._page_height, height)
        self.assertEqual(window._cover.get_allocation().height, height)
        self.assertEqual(window._password_page.get_allocation().height, height)
        self.assertEqual(window._password_page.get_allocation().y,
                         height - round(window._offset.get_value()))
        self.assertEqual(window._pages.get_vadjustment().get_value(), 0)

    def test_keyboard_resize_during_return_drag_preserves_progress(self):
        window = self.screen("mobile")
        window._set_page(1, animate=False)
        window.resize(360, 420)
        self.settle()
        self.assert_page_geometry(window)
        height = window._page_height
        self.assertLess(height, 720)
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_update(Mock(), 0, height * 0.3)
        progress = window._offset.get_value() / height
        # Simulate the keyboard's exclusive zone disappearing while dragging.
        window.resize(360, 720)
        self.settle()
        self.assert_page_geometry(window)
        self.assertAlmostEqual(window._offset.get_value() / window._page_height, progress)
        self.assertTrue(window._password_page.get_sensitive())
        self.assertIs(window.get_focus(), window._entry)
        window._on_drag_update(Mock(), 0, height * 0.3 + 40)
        self.assertAlmostEqual(window._offset.get_value(), progress * window._page_height - 40)
        window._on_drag_end(Mock(), 0, height * 0.3 + 40)
        self.settle()
        self.assertEqual(window._page, 0)
        self.assertEqual(window._offset.get_value(), 0)
        self.assert_page_geometry(window)

    def test_keyboard_resize_during_snap_leaves_password_fully_offscreen(self):
        window = self.screen("mobile")
        window._set_page(1, animate=False)
        for _ in range(3):
            window.resize(360, 420)
            self.settle()
            window._show_clock()
            window.resize(360, 720)
            self.settle()
            self.assertEqual(window._page, 0)
            self.assertEqual(window._offset.get_value(), 0)
            self.assert_page_geometry(window)
            self.assertFalse(window._password_page.get_sensitive())
            window._set_page(1, animate=False)

    def test_desktop_typing_and_escape(self):
        window = self.screen("desktop")
        window._on_key_press(window, SimpleNamespace(keyval=self.Gdk.KEY_a, string="a"))
        self.assertEqual(window._page, 1)
        self.assertEqual(window._entry.get_text(), "a")
        window._on_key_press(window, SimpleNamespace(keyval=self.Gdk.KEY_Escape))
        self.assertEqual(window._page, 0)
        self.assertEqual(window._entry.get_text(), "")


if __name__ == "__main__":
    unittest.main()
