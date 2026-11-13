"""Deterministic resize and keyboard-callback regressions without a display."""

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cairo

from test_locker_password import load_locker


class Position:
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def configure(self, value, *_args):
        self.value = value


class LockerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_locker()
        cls.module.Gtk.EventSequenceState = SimpleNamespace(CLAIMED=1)
        cls.module.GLib.Error = RuntimeError
        cls.module.GLib.get_monotonic_time.return_value = 0

    def screen(self, page=1):
        window = object.__new__(self.module.LockScreen)
        window._page = page
        window._desktop = False
        window._page_height = 420
        window._page_size = (360, 420)
        window._snap_source = None
        window._drag_active = False
        window._drag_claimed = False
        window._drag_moved = False
        window._drag_dy = 0
        window._authenticating = False
        window._closing = False
        window._keyboard_request = 0
        window._offset = Position(page * 420)
        for name in ('_pages', '_entry', '_password_page', '_status', 'set_focus'):
            setattr(window, name, Mock())
        window._pages.is_moving.return_value = False
        window.get_mapped = lambda: True
        return window

    def test_resize_during_drag_preserves_fraction_without_refocusing(self):
        window = self.screen()
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_update(Mock(), 0, 126)
        self.assertEqual(window._offset.get_value(), 294)
        window._layout_pages(None, SimpleNamespace(width=360, height=720))
        self.assertAlmostEqual(window._offset.get_value(), 504)
        window._entry.grab_focus.assert_not_called()
        window._password_page.set_sensitive.assert_not_called()
        window._on_drag_update(Mock(), 0, 166)
        self.assertAlmostEqual(window._offset.get_value(), 464)
        window._on_drag_end(Mock(), 0, 166)
        self.assertEqual(window._page, 0)
        self.assertFalse(window._drag_active)
        self.assertIsNotNone(window._snap_source)
        window._animate_snap(None, SimpleNamespace(get_frame_time=lambda: 10**30))
        self.assertEqual(window._offset.get_value(), 0)
        window._entry.grab_focus.assert_not_called()
        window._password_page.set_sensitive.assert_called_with(False)

    def test_captured_button_tap_does_not_cancel_child_click(self):
        window = self.screen()
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_end(Mock(), 0, 0)
        self.assertFalse(window._drag_active)
        window._password_page.set_sensitive.assert_not_called()
        window._entry.grab_focus.assert_not_called()
        window.set_focus.assert_not_called()
        self.assertIsNone(window._snap_source)

    def test_swipe_keeps_its_input_target_and_keyboard_until_release(self):
        for page, dy in ((0, -150), (1, 150)):
            with self.subTest(page=page):
                window = self.screen(page)
                # GTK may cancel the sequence when its pressed child becomes
                # insensitive or the keyboard/focus change unmaps its surface.
                def cancel(*_args):
                    window._on_drag_cancel()
                window._password_page.set_sensitive.side_effect = cancel
                window.set_focus.side_effect = cancel
                window._set_keyboard_visible = Mock(side_effect=cancel)
                window._on_drag_begin(Mock(), 100, 200)
                window._on_drag_update(Mock(), 0, dy)
                self.assertTrue(window._drag_active)
                self.assertTrue(window._drag_claimed)
                self.assertEqual(window._offset.get_value(), page * 420 - dy)
                window._password_page.set_sensitive.assert_not_called()
                window.set_focus.assert_not_called()
                window._set_keyboard_visible.assert_not_called()
                window._on_drag_end(Mock(), 0, dy)
                self.assertFalse(window._drag_active)
                self.assertEqual(window._page, 1 - page)

    def test_cancelled_sequences_ignore_late_motion_and_release_then_allow_new_swipe(self):
        window = self.screen(0)
        window._on_drag_begin(Mock(), 100, 300)
        window._on_drag_update(Mock(), 0, -100)
        window._on_drag_cancel()
        snap = window._snap_source
        window._on_drag_update(Mock(), 0, -300)
        window._on_drag_end(Mock(), 0, -300)
        self.assertEqual(window._page, 0)
        self.assertEqual(window._offset.get_value(), 100)
        self.assertIs(window._snap_source, snap)
        window._on_drag_begin(Mock(), 100, 300)
        window._on_drag_update(Mock(), 0, -200)
        window._on_drag_end(Mock(), 0, -200)
        self.assertEqual(window._page, 1)
        window._animate_snap(None, SimpleNamespace(get_frame_time=lambda: 10**30))
        self.assertEqual(window._offset.get_value(), 420)

    def test_remap_resets_claimed_gesture_and_restores_swiping(self):
        window = self.screen(0)
        window._drag = Mock()
        window._on_drag_begin(Mock(), 100, 300)
        window._on_drag_update(Mock(), 0, -100)
        window._on_unmap()
        self.assertFalse(window._drag_active)
        self.assertFalse(window._drag_claimed)
        self.assertEqual(window._offset.get_value(), 0)
        window._on_map()
        window._on_drag_begin(Mock(), 100, 300)
        window._on_drag_update(Mock(), 0, -200)
        self.assertEqual(window._offset.get_value(), 200)
        window._on_drag_end(Mock(), 0, -200)
        self.assertEqual(window._page, 1)

    def test_repeated_swipes_in_both_directions_after_cancellation(self):
        window = self.screen(0)
        for _ in range(5):
            for page, dy in ((0, -150), (1, 150)):
                self.assertEqual(window._page, page)
                window._on_drag_begin(Mock(), 100, 200)
                window._on_drag_cancel()
                window._on_drag_begin(Mock(), 100, 200)
                window._on_drag_update(Mock(), 0, dy)
                window._on_drag_end(Mock(), 0, dy)
                window._animate_snap(None, SimpleNamespace(get_frame_time=lambda: 10**30))
                self.assertEqual(window._offset.get_value(), (1 - page) * 420)

    def test_child_claiming_tap_does_not_disable_or_refocus_controls(self):
        window = self.screen()
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_cancel(Mock(), None)
        self.assertFalse(window._drag_active)
        window._password_page.set_sensitive.assert_not_called()
        window._entry.grab_focus.assert_not_called()
        window.set_focus.assert_not_called()
        self.assertIsNone(window._snap_source)

    def test_cancelled_partial_drag_still_settles_page(self):
        window = self.screen()
        window._on_drag_begin(Mock(), 100, 100)
        window._on_drag_update(Mock(), 0, 100)
        window._on_drag_cancel(Mock(), None)
        self.assertFalse(window._drag_active)
        self.assertIsNotNone(window._snap_source)
        window._animate_snap(None, SimpleNamespace(get_frame_time=lambda: 10**30))
        self.assertEqual(window._offset.get_value(), 420)
        window._password_page.set_sensitive.assert_called_with(True)

    def test_resize_of_settled_password_does_not_reopen_input_method(self):
        window = self.screen()
        window._layout_pages(None, SimpleNamespace(width=360, height=720))
        self.assertEqual(window._offset.get_value(), 720)
        window._entry.grab_focus.assert_not_called()
        window._password_page.set_sensitive.assert_not_called()

    def test_resize_during_snap_pins_final_endpoint(self):
        window = self.screen()
        window._offset.set_value(180)
        window._set_page(0)
        window._layout_pages(None, SimpleNamespace(width=360, height=720))
        self.assertAlmostEqual(window._offset.get_value(), 180 / 420 * 720)
        window._animate_snap(None, SimpleNamespace(get_frame_time=lambda: 10**30))
        self.assertEqual(window._offset.get_value(), 0)
        window._entry.grab_focus.assert_not_called()

    def test_pending_show_request_is_discarded_after_return_to_clock(self):
        window = self.screen()
        window._set_keyboard_visible(True)
        window._set_page(0, animate=False)
        bus = self.module.Gio.bus_get_finish.return_value
        bus.reset_mock()
        window._keyboard_bus_ready(None, Mock(), (1, True))
        bus.call.assert_not_called()
        window._keyboard_bus_ready(None, Mock(), (2, False))
        bus.call.assert_called_once()

    def test_keyboard_cannot_open_mid_drag_mid_snap_or_after_destroy(self):
        for state in ('drag', 'snap', 'destroy'):
            with self.subTest(state=state):
                window = self.screen()
                window._set_keyboard_visible(True)
                if state == 'drag':
                    window._drag_active = True
                elif state == 'snap':
                    window._snap_source = 1
                else:
                    window._on_destroy()
                bus = self.module.Gio.bus_get_finish.return_value
                bus.reset_mock()
                window._keyboard_bus_ready(None, Mock(), (1, True))
                bus.call.assert_not_called()

    def test_layout_positions_both_pages_without_native_scrolling(self):
        layout = object.__new__(self.module.LockPageLayout)
        layout._position = Position(360)
        layout._prepare_resize = Mock()
        layout._moving = False
        layout._rest_page = 0
        layout._surfaces = {}
        layout._surface_geometry = None
        layout.get_scale_factor = lambda: 1
        layout.set_size = Mock()
        layout.move = Mock()
        pages = [Mock(), Mock()]
        layout.get_children = lambda: pages
        # Supply the base allocation method; no GTK runtime is needed.
        class LayoutBase:
            do_size_allocate = Mock()
        self.module.Gtk.Layout = LayoutBase
        allocation = SimpleNamespace(width=360, height=720)
        layout.do_size_allocate(allocation)
        layout.set_size.assert_called_once_with(360, 720)
        self.assertEqual([call.args[2] for call in layout.move.call_args_list], [-360, 360])
        for page in pages:
            page.set_size_request.assert_called_once_with(360, 720)
        layout._position.set_value(0)
        layout.move.reset_mock()
        layout.do_size_allocate(allocation)
        self.assertEqual([call.args[2] for call in layout.move.call_args_list], [0, 720])


    def moving_layout(self):
        layout = object.__new__(self.module.LockPageLayout)
        layout._position = Position(0)
        layout._prepare_resize = Mock()
        layout._moving = False
        layout._rest_page = 0
        layout._surfaces = {}
        layout._surface_geometry = None
        layout.get_scale_factor = Mock(return_value=1)
        layout.get_allocated_width = lambda: 4
        layout.get_allocated_height = lambda: 8
        layout.get_bin_window = Mock()
        layout.get_style_context = Mock()
        layout.queue_allocate = Mock()
        layout.queue_draw = Mock()
        layout.set_size = Mock()
        layout.move = Mock()
        pages = [Mock(), Mock()]
        for page, color in zip(pages, ((1, 0, 0), (0, 0, 1))):
            page.get_allocated_width.return_value = 4
            page.get_allocated_height.return_value = 8
            def draw(cr, color=color):
                cr.set_source_rgb(*color)
                cr.paint()
            page.draw.side_effect = draw
        layout.get_children = lambda: pages
        return layout, pages

    def test_drag_drawing_moves_pixels_without_relocating_input_windows(self):
        layout, pages = self.moving_layout()
        allocation = SimpleNamespace(width=4, height=8)
        with patch.object(self.module.Gtk, 'Layout', SimpleNamespace(do_size_allocate=Mock())), \
             patch.object(self.module.Gtk, 'cairo_should_draw_window', return_value=True, create=True), \
             patch.object(self.module.Gtk, 'cairo_transform_to_window', create=True), \
             patch.object(self.module.Gtk, 'render_background', create=True):
            layout.do_size_allocate(allocation)
            layout.begin_motion()
            for offset in (3, 6):
                layout._position.set_value(offset)
                layout._position_changed()
                layout.queue_allocate.assert_not_called()
                # Other GTK allocations during a swipe must also keep the
                # input windows at their original page positions.
                layout.move.reset_mock()
                layout.do_size_allocate(allocation)
                self.assertEqual([call.args[2] for call in layout.move.call_args_list], [0, 8])
                target = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 8)
                layout.do_draw(cairo.Context(target))
                target.flush()
                pixels = target.get_data()
                self.assertEqual(int.from_bytes(pixels[0:4], sys.byteorder), 0xffff0000)
                bottom = 7 * target.get_stride()
                self.assertEqual(int.from_bytes(pixels[bottom:bottom+4], sys.byteorder), 0xff0000ff)
            for page in pages:
                page.draw.assert_called_once()
            layout._position.set_value(8)
            layout.end_motion()
            self.assertFalse(layout.is_moving())
            self.assertEqual(layout._surfaces, {})
            layout.do_size_allocate(allocation)
            self.assertEqual([call.args[2] for call in layout.move.call_args_list[-2:]], [-8, 0])

    def test_page_larger_than_keyboard_viewport_still_slides(self):
        layout, pages = self.moving_layout()
        for page in pages:
            page.get_allocated_height.return_value = 12
        layout.begin_motion()
        layout._position.set_value(3)
        with patch.object(self.module.Gtk, 'cairo_should_draw_window', return_value=True, create=True), \
             patch.object(self.module.Gtk, 'cairo_transform_to_window', create=True), \
             patch.object(self.module.Gtk, 'render_background', create=True):
            target = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 8)
            layout.do_draw(cairo.Context(target))
        self.assertEqual(len(layout._surfaces), 2)
        for surface in layout._surfaces.values():
            self.assertEqual(surface.get_height(), 8)

    def test_password_page_touch_target_stays_at_origin_during_return_swipe(self):
        layout, _pages = self.moving_layout()
        allocation = SimpleNamespace(width=4, height=8)
        with patch.object(self.module.Gtk, 'Layout', SimpleNamespace(do_size_allocate=Mock())):
            layout._position.set_value(8)
            layout.do_size_allocate(allocation)
            layout.begin_motion()
            for offset in (6, 3, 0):
                layout._position.set_value(offset)
                layout.move.reset_mock()
                layout.do_size_allocate(allocation)
                self.assertEqual([call.args[2] for call in layout.move.call_args_list], [-8, 0])
            layout.end_motion()
            layout.do_size_allocate(allocation)
            self.assertEqual([call.args[2] for call in layout.move.call_args_list[-2:]], [0, 8])

    def test_interrupted_snap_near_endpoint_still_restores_live_controls(self):
        window = self.screen(0)
        window._page = 1
        window._offset.set_value(419.8)
        window._pages.is_moving.return_value = True
        window._on_drag_begin(Mock(), 100, 200)
        window._on_drag_end(Mock(), 0, 0)
        self.assertEqual(window._offset.get_value(), 420)
        window._pages.end_motion.assert_called_once()
        window._password_page.set_sensitive.assert_called_with(True)


if __name__ == '__main__':
    unittest.main()
