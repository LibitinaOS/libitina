"""Opt-in GTK drawing checks: YUI_RUN_GTK_TESTS=1, with a working display."""

import ast
import math
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get("YUI_RUN_GTK_TESTS") == "1", "requires a GTK display")
class HomePageDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cairo
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib
        Gtk.init([])
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        cls.Gtk, cls.GLib, cls.cairo = Gtk, GLib, cairo
        tree = ast.parse((Path(__file__).resolve().parents[1] / "mobile.py").read_text())
        strip = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == "HomePageStrip")
        namespace = dict(Gtk=Gtk, cairo=cairo, math=math)
        exec(compile(ast.Module(body=[strip], type_ignores=[]), "mobile.py", "exec"), namespace)
        cls.Strip = namespace["HomePageStrip"]

    def settle(self):
        loop = self.GLib.MainLoop()
        self.GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
        loop.run()

    def setUp(self):
        self.window = self.Gtk.Window()
        self.addCleanup(self.window.destroy)
        self.window.set_default_size(320, 480)
        self.strip = self.Strip()
        self.window.add(self.strip)
        self.pages = []
        self.draws = [0] * 8
        for i in range(8):
            page = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL)
            for j in range(6):
                button = self.Gtk.Button(label=f"Page {i} — Application {j}")
                page.pack_start(button, True, True, 0)
            page.connect("draw", self.count_draw, i)
            self.strip.put(page, i * 320, 0)
            self.pages.append(page)
        self.window.show_all()
        self.settle()
        self.width = self.strip.get_allocated_width()
        self.height = self.strip.get_allocated_height()
        for i, page in enumerate(self.pages):
            page.set_size_request(self.width, self.height)
            self.strip.move(page, i * self.width, 0)
        self.strip.set_size(8 * self.width, self.height)
        self.settle()

    def count_draw(self, _page, _cr, i):
        self.draws[i] += 1
        return False

    def render(self):
        scale = self.strip.get_scale_factor()
        surface = self.cairo.ImageSurface(self.cairo.FORMAT_ARGB32, self.width * scale, self.height * scale)
        surface.set_device_scale(scale, scale)
        self.strip.draw(self.cairo.Context(surface))
        surface.flush()
        return bytes(surface.get_data())

    def test_cached_pages_match_live_widgets_at_scroll_offsets(self):
        for offset in (0, 37, self.width // 2, self.width, self.width + 53):
            with self.subTest(offset=offset):
                self.strip.end_motion()
                self.strip.get_hadjustment().set_value(offset)
                self.settle()
                live = self.render()
                self.strip.begin_motion()
                cached = self.render()
                # Intermediate surfaces can slightly change text antialiasing.
                differences = [abs(a - b) for a, b in zip(live, cached)]
                self.assertLessEqual(max(differences), 8)
                self.assertLess(sum(differences) / len(differences), 0.1)
                self.assertTrue(any(cached))

    def test_motion_reuses_visible_pages_and_bounds_memory(self):
        self.strip.begin_motion()
        self.strip.get_hadjustment().set_value(37)
        self.render()
        before = sum(self.draws)
        for offset in range(40, 120, 4):
            self.strip.get_hadjustment().set_value(offset)
            self.render()
        self.assertEqual(sum(self.draws), before, "motion must reuse rendered icons and labels")
        self.assertEqual(len(self.strip._surfaces), 2)
        for i in range(1, 7):
            self.strip.get_hadjustment().set_value(i * self.width + 37)
            self.render()
            self.assertEqual(len(self.strip._surfaces), 2)
        self.strip.end_motion()
        self.assertFalse(self.strip._surfaces)
        before = sum(self.draws)
        self.render()
        self.assertGreater(sum(self.draws), before)

    def test_resize_replaces_cached_surfaces(self):
        self.strip.begin_motion()
        self.render()
        old_surface = self.strip._surfaces[0]
        self.window.resize(self.width + 40, self.height + 40)
        self.settle()
        self.width = self.strip.get_allocated_width()
        self.height = self.strip.get_allocated_height()
        for i, page in enumerate(self.pages):
            page.set_size_request(self.width, self.height)
            self.strip.move(page, i * self.width, 0)
        self.strip.set_size(8 * self.width, self.height)
        self.settle()
        self.render()
        self.assertIsNot(self.strip._surfaces[0], old_surface)
        self.assertEqual(self.strip._surfaces[0].get_width(), self.width * self.strip.get_scale_factor())


if __name__ == "__main__":
    unittest.main()
