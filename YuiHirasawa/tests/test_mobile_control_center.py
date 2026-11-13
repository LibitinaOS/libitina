"""Mobile shade gesture and overlay configuration without a compositor."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_shade():
    repository = ModuleType('gi.repository')
    repository.Gdk = SimpleNamespace(BUTTON_PRIMARY=1)
    repository.GLib = SimpleNamespace(source_remove=Mock(), timeout_add=Mock(return_value=9),
                                      get_monotonic_time=Mock(return_value=0),
                                      SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
    repository.Gtk = SimpleNamespace(EventSequenceState=SimpleNamespace(CLAIMED='claimed'))
    repository.GtkLayerShell = Mock()
    shared = ModuleType('control_center')

    class Controls:
        def _set_locked(self, locked):
            self.locked = locked
            if locked and self.dialog:
                self.dialog.destroy()

        def _create_dialog(self):
            self.dialog = Mock()

        def _dialog_closed(self):
            self.dialog = None

    shared.StatusControls = Controls
    spec = importlib.util.spec_from_file_location('mobile_shade_under_test',
        Path(__file__).resolve().parents[1] / 'mobile_control_center.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gi': ModuleType('gi'), 'gi.repository': repository,
                                  'control_center': shared}):
        spec.loader.exec_module(module)
    return module, repository


class MobileControlCenterTests(unittest.TestCase):
    def setUp(self):
        self.module, self.repo = load_shade()
        layout_patch = patch.object(self.module, 'load_layout', return_value={
            'safe_inset_top': 0, 'safe_inset_bottom': 0,
            'safe_inset_left': 0, 'safe_inset_right': 0})
        layout_patch.start()
        self.addCleanup(layout_patch.stop)
        self.shade = self.module.MobileStatusControls.__new__(self.module.MobileStatusControls)
        self.shade.parent = Mock()
        self.shade.locked = False
        self.shade._pull = Mock()
        self.shade.parent.get_allocated_height.return_value = 16
        self.shade._height = 800
        self.shade._reveal = 0
        self.shade._animation = None
        self.shade._dragging = False
        self.shade._eligible = False
        self.shade.dialog = None
        self.shade.refresh = Mock()
        self.shade._gesture = Mock(return_value=Mock())
        self.monitor = Mock()
        self.monitor.get_geometry.return_value = SimpleNamespace(width=400, height=800)
        self.shade.parent.get_display.return_value.get_monitor_at_window.return_value = self.monitor
        self.gesture = Mock()

    def pull(self, y=5, dx=0, dy=150):
        self.shade._pull_begin(self.gesture, 20, y)
        self.shade._drag_update(self.gesture, dx, dy)

    def finish(self):
        self.repo.GLib.get_monotonic_time.return_value = 220_000
        self.assertFalse(self.shade._animate())

    def test_only_status_bar_start_can_open(self):
        for y in (-1, 17, 400):
            self.pull(y=y)
            self.assertIsNone(self.shade.dialog)
        self.gesture.set_state.assert_not_called()

    def test_lock_blocks_status_clicks_and_pull_gestures(self):
        self.shade._set_locked(True)
        self.shade.show()
        self.pull()
        self.shade._drag_end(self.gesture, 0, 150)
        self.assertIsNone(self.shade.dialog)
        self.gesture.set_state.assert_not_called()

    def test_lock_closes_in_progress_shade_and_unlock_restores_pulling(self):
        self.pull()
        dialog = self.shade.dialog
        self.shade._set_locked(True)
        dialog.destroy.assert_called_once()
        self.assertFalse(self.shade._dragging)
        self.assertFalse(self.shade._eligible)
        self.shade._pull.reset.assert_called_once()
        self.shade._dialog_closed()
        self.shade._set_locked(False)
        self.pull()
        self.assertIsNotNone(self.shade.dialog)

    def test_configured_status_height_and_shade_safe_area(self):
        self.shade.parent.get_allocated_height.return_value = 64
        layout = {"safe_inset_top": 40, "safe_inset_bottom": 12,
                  "safe_inset_left": 8, "safe_inset_right": 10}
        with patch.object(self.module, 'load_layout', return_value=layout):
            self.pull(y=60)
        self.assertIsNotNone(self.shade.dialog)
        contents = self.shade.dialog.get_child.return_value
        contents.set_margin_top.assert_called_with(40)
        contents.set_margin_bottom.assert_called_with(12)
        contents.set_margin_start.assert_called_with(8)
        contents.set_margin_end.assert_called_with(10)

    def test_horizontal_upward_and_tap_do_not_open(self):
        for dx, dy in ((0, 4), (0, -80), (100, 30)):
            self.pull(dx=dx, dy=dy)
            self.assertIsNone(self.shade.dialog)

    def test_pull_tracks_distance_and_claims_before_release(self):
        self.pull()
        self.gesture.set_state.assert_called_once_with('claimed')
        self.assertEqual(self.shade._reveal, 150)
        self.shade.dialog.present.assert_not_called()
        self.repo.GtkLayerShell.set_margin.assert_called_with(
            self.shade.dialog, self.repo.GtkLayerShell.Edge.TOP, -650)
        self.shade._drag_update(self.gesture, 0, 300)
        self.assertEqual(self.shade._reveal, 300)

    def test_release_opens_full_screen_overlay(self):
        self.pull()
        dialog = self.shade.dialog
        dialog.set_size_request.assert_called_with(-1, 800)
        self.repo.GtkLayerShell.set_exclusive_zone.assert_called_with(dialog, -1)
        for edge in (self.repo.GtkLayerShell.Edge.TOP, self.repo.GtkLayerShell.Edge.LEFT,
                     self.repo.GtkLayerShell.Edge.RIGHT):
            self.repo.GtkLayerShell.set_anchor.assert_any_call(dialog, edge, True)
        self.shade._drag_end(self.gesture, 0, 150)
        self.finish()
        self.assertEqual(self.shade._reveal, 800)
        dialog.present.assert_called_once()

    def test_short_pull_and_cancellation_close(self):
        self.pull(dy=20)
        dialog = self.shade.dialog
        self.shade._drag_end(self.gesture, 0, 20)
        self.finish()
        dialog.destroy.assert_called_once()

    def test_cancelled_pull_returns_closed(self):
        self.pull()
        dialog = self.shade.dialog
        self.shade._drag_cancel(self.gesture, None)
        self.finish()
        dialog.destroy.assert_called_once()

    def test_close_removes_pending_animation(self):
        self.pull()
        self.shade._settle(True)
        self.shade._dialog_closed()
        self.repo.GLib.source_remove.assert_called_once_with(9)
        self.assertIsNone(self.shade.dialog)
        self.assertEqual(self.shade._reveal, 0)


if __name__ == '__main__':
    unittest.main()
