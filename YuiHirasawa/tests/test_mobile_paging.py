"""Exercise pager movement and settling without a Wayland compositor."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def pager_class():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "mobile.py").read_text())
    launcher = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "ApplicationLauncher")
    names = {"_set_page", "_stop_page_snap", "_animate_page_snap", "_on_pager_unmap", "_on_pager_map",
             "_start_swipe_launch_cooldown"}
    launcher.body = [node for node in launcher.body if isinstance(node, ast.FunctionDef)
                     and (node.name.startswith("_on_page_drag_") or node.name in names)]
    launcher.bases = []
    glib = SimpleNamespace(source_remove=Mock(), timeout_add=Mock(return_value=7),
                           get_monotonic_time=Mock(return_value=1_000_000),
                           SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
    namespace = {"Gtk": SimpleNamespace(EventSequenceState=SimpleNamespace(CLAIMED="claimed")),
                 "GLib": glib}
    exec(compile(ast.Module(body=[launcher], type_ignores=[]), "mobile.py", "exec"), namespace)
    namespace["ApplicationLauncher"].SWIPE_LAUNCH_COOLDOWN_US = 3_000_000
    return namespace["ApplicationLauncher"], glib


class MobilePagingTests(unittest.TestCase):
    def setUp(self):
        cls, self.glib = pager_class()
        self.pager = cls()
        self.pager._current_page = 1
        self.pager._page_width = 400
        self.pager._page_dots = [Mock(), Mock(), Mock()]
        self.pager._snap_source = None
        self.pager._pages = Mock()
        self.pager._swipe = Mock()
        self.pager.get_mapped = Mock(return_value=True)
        self.pager._pages.add_tick_callback.return_value = 7
        self.clock = Mock()
        self.clock.get_frame_time.side_effect = lambda: self.glib.get_monotonic_time()
        self.position = 400
        self.pager._page_adjustment = Mock()
        self.pager._page_adjustment.get_value.side_effect = lambda: self.position
        self.pager._page_adjustment.set_value.side_effect = self.move
        self.gesture = Mock()
        self.pager._on_page_drag_begin(self.gesture, 0, 0)

    def move(self, value):
        self.position = value

    def drag(self, dx, dy=0):
        self.pager._on_page_drag_update(self.gesture, dx, dy)

    def release(self, dx, dy=0):
        self.pager._on_page_drag_end(self.gesture, dx, dy)

    def finish_animation(self):
        self.glib.get_monotonic_time.return_value = 1_220_000
        self.assertFalse(self.pager._animate_page_snap(self.pager._pages, self.clock))

    def test_pages_follow_finger_in_both_directions_before_release(self):
        self.drag(-60)
        self.assertEqual(self.position, 460)
        self.drag(-150)
        self.assertEqual(self.position, 550)
        self.drag(40)
        self.assertEqual(self.position, 360)
        self.assertEqual(self.pager._current_page, 1)
        self.gesture.set_state.assert_called_once_with("claimed")

    def test_release_animates_to_next_page(self):
        self.drag(-100)
        self.release(-100)
        self.assertEqual(self.pager._current_page, 2)
        self.assertEqual(self.position, 500)
        self.glib.get_monotonic_time.return_value = 1_110_000
        self.assertTrue(self.pager._animate_page_snap(self.pager._pages, self.clock))
        self.assertGreater(self.position, 500)
        self.assertLess(self.position, 800)
        self.finish_animation()
        self.assertEqual(self.position, 800)
        self.assertIsNone(self.pager._snap_source)

    def test_short_drag_snaps_back(self):
        self.drag(30)
        self.release(30)
        self.assertEqual(self.pager._current_page, 1)
        self.finish_animation()
        self.assertEqual(self.position, 400)

    def test_right_drag_goes_back(self):
        self.drag(100)
        self.release(100)
        self.finish_animation()
        self.assertEqual(self.position, 0)

    def test_taps_and_vertical_motion_do_not_move_or_claim(self):
        for dx, dy in ((0, 0), (5, 2), (30, 90)):
            self.drag(dx, dy)
            self.assertEqual(self.position, 400)
        self.gesture.set_state.assert_not_called()

    def test_drag_cannot_pass_first_or_last_page(self):
        self.drag(900)
        self.assertEqual(self.position, 0)
        self.drag(-900)
        self.assertEqual(self.position, 800)

    def test_cancel_snaps_back(self):
        self.drag(-100)
        self.pager._on_page_drag_cancel(self.gesture, None)
        self.finish_animation()
        self.assertEqual(self.position, 400)
        self.assertFalse(self.pager._page_drag_claimed)

    def test_new_drag_interrupts_animation_without_jumping(self):
        self.drag(-100)
        self.release(-100)
        self.pager._on_page_drag_begin(self.gesture, 0, 0)
        self.pager._pages.remove_tick_callback.assert_called_once_with(7)
        self.assertEqual(self.position, 500)
        self.drag(-20)
        self.assertEqual(self.position, 520)

    def test_unmap_stops_animation_and_aligns_page(self):
        self.drag(-100)
        self.release(-100)
        self.pager._on_pager_unmap(Mock())
        self.pager._pages.remove_tick_callback.assert_called_once_with(7)
        self.assertEqual(self.position, 800)

    def test_long_drag_settles_at_visible_page(self):
        self.pager._page_dots.extend([Mock(), Mock()])
        self.drag(-850)
        self.release(-850)
        self.assertEqual(self.pager._current_page, 3)
        self.finish_animation()
        self.assertEqual(self.position, 1200)

    def test_snap_uses_display_clock_and_restores_live_buttons(self):
        self.drag(-100)
        self.release(-100)
        self.pager._pages.add_tick_callback.assert_called_once_with(self.pager._animate_page_snap)
        self.glib.timeout_add.assert_not_called()
        self.pager._pages.end_motion.reset_mock()
        self.finish_animation()
        self.pager._pages.end_motion.assert_called_once()

    def test_hide_mid_drag_resets_input_and_launch_block(self):
        self.drag(-100)
        self.pager.get_mapped.return_value = False
        self.pager._on_pager_unmap(Mock())
        self.assertFalse(self.pager._page_drag_claimed)
        self.assertFalse(self.pager._page_drag_moved)
        self.assertEqual(self.pager._launch_blocked_until, 0)
        self.assertEqual(self.position, 400)
        self.assertIsNone(self.pager._snap_source)
        self.pager._swipe.reset.assert_called_once()

    def test_hidden_cancel_does_not_restart_animation(self):
        self.drag(-100)
        self.pager.get_mapped.return_value = False
        self.pager._on_page_drag_cancel(self.gesture, None)
        self.assertEqual(self.position, 400)
        self.pager._pages.add_tick_callback.assert_not_called()

    def test_reopen_clears_stale_gesture_and_finishes_snap(self):
        self.drag(-100)
        self.release(-100)
        self.pager._page_drag_moved = True
        self.pager._on_pager_map(Mock())
        self.assertFalse(self.pager._page_drag_claimed)
        self.assertFalse(self.pager._page_drag_moved)
        self.assertEqual(self.pager._launch_blocked_until, 0)
        self.assertEqual(self.position, 800)
        self.assertIsNone(self.pager._snap_source)


if __name__ == "__main__":
    unittest.main()
