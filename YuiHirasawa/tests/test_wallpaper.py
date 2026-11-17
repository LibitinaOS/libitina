"""Exercise wallpaper cover geometry without a running Wayland compositor."""

import importlib.util
import json
from pathlib import Path
import sys
import subprocess
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def renderer_module():
    gi = ModuleType("gi")
    gi.require_version = Mock()
    gi.require_foreign = Mock()
    repository = ModuleType("gi.repository")
    repository.Gtk = SimpleNamespace(Image=object)
    repository.Gdk = Mock()
    repository.GdkPixbuf = Mock()
    repository.GLib = Mock()
    repository.GtkLayerShell = Mock()
    spec = importlib.util.spec_from_file_location("wallpaper_under_test", Path(__file__).resolve().parents[1] / "wallpaper.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        spec.loader.exec_module(module)
    return module


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.module = renderer_module()
        self.image = Mock()
        self.image._source = Mock()
        self.image._source.get_width.return_value = 1920
        self.image._source.get_height.return_value = 1080
        self.image._scaled_size = None
        self.image.get_scale_factor.return_value = 1

    def update(self, width, height):
        self.module.Wallpaper._update_image(self.image, width, height)

    def test_portrait_center_crop_and_resize(self):
        self.update(360, 640)
        self.image._source.scale_simple.assert_called_once_with(
            1138, 640, self.module.GdkPixbuf.InterpType.BILINEAR)
        self.module.GdkPixbuf.Pixbuf.new_subpixbuf.assert_called_once_with(
            self.image._source.scale_simple.return_value, 389, 0, 360, 640)
        self.image.set_from_surface.assert_called_once_with(
            self.module.Gdk.cairo_surface_create_from_pixbuf.return_value)
        self.update(640, 360)
        self.assertEqual(self.image._source.scale_simple.call_count, 2)
        self.assertEqual(self.image._scaled_size, (640, 360, 1))

    def test_same_allocation_is_cached(self):
        self.update(800, 600)
        self.update(800, 600)
        self.image._source.scale_simple.assert_called_once()

    def test_disabled_or_zero_size_does_not_scale(self):
        self.update(0, 640)
        self.update(360, 0)
        self.image._source.scale_simple.assert_not_called()
        self.image._source = None
        self.update(360, 640)
        self.image.set_from_surface.assert_not_called()

    def test_hidpi_wallpaper_is_prepared_at_device_resolution_once(self):
        self.image.get_scale_factor.return_value = 2
        self.update(360, 640)
        self.image._source.scale_simple.assert_called_once_with(
            2276, 1280, self.module.GdkPixbuf.InterpType.BILINEAR)
        self.module.GdkPixbuf.Pixbuf.new_subpixbuf.assert_called_once_with(
            self.image._source.scale_simple.return_value, 778, 0, 720, 1280)
        self.module.Gdk.cairo_surface_create_from_pixbuf.assert_called_once_with(
            self.module.GdkPixbuf.Pixbuf.new_subpixbuf.return_value, 2,
            self.image.get_window.return_value)
        self.update(360, 640)
        self.image._source.scale_simple.assert_called_once()
        self.image.get_scale_factor.return_value = 3
        self.update(360, 640)
        self.assertEqual(self.image._source.scale_simple.call_count, 2)
        self.assertEqual(self.image._scaled_size, (360, 640, 3))

    def test_wallpaper_does_not_request_image_dimensions(self):
        self.assertEqual(self.module.Wallpaper.do_get_preferred_width(self.image), (0, 0))
        self.assertEqual(self.module.Wallpaper.do_get_preferred_height(self.image), (0, 0))


class WatermarkTests(unittest.TestCase):
    def setUp(self):
        self.module = renderer_module()

    def watermark(self, git_results, metadata):
        with patch.object(self.module.subprocess, "check_output", side_effect=git_results), \
                patch.object(self.module.Path, "read_text", return_value=metadata) as read, \
                patch.object(self.module.platform, "freedesktop_os_release",
                             return_value={"PRETTY_NAME": "Test Linux"}), \
                patch.object(self.module.platform, "release", return_value="test-kernel"):
            result = self.module.internal_watermark_text()
        return result, read

    def test_live_checkout_takes_precedence(self):
        result, read = self.watermark(["live-branch\n", "live-commit\n"], "")
        self.assertEqual(result, "Test Linux\ntest-kernel\nb=live-branch c=live-commit\nI <3 Jamie Paige")
        read.assert_not_called()

    def test_missing_git_uses_build_metadata(self):
        metadata = json.dumps({"git_branch": "built-branch", "git_commit": "a" * 40})
        result, read = self.watermark([FileNotFoundError(), FileNotFoundError()], metadata)
        self.assertIn(f"b=built-branch c={'a' * 40}", result)
        read.assert_called_once_with()

    def test_fallback_only_replaces_unavailable_values(self):
        result, _ = self.watermark(
            ["live-branch", subprocess.CalledProcessError(128, "git")],
            '{"git_branch": "built-branch", "git_commit": "built-commit"}')
        self.assertIn("b=live-branch c=built-commit", result)

    def test_unusable_metadata_keeps_unknown_values(self):
        for metadata in ("", "{", "[]", "null", "{}",
                         '{"git_branch": 1, "git_commit": " "}'):
            with self.subTest(metadata=metadata):
                result, _ = self.watermark([OSError(), OSError()], metadata)
                self.assertIn("b=unknown c=unknown", result)

    def test_missing_or_unreadable_metadata_keeps_unknown_values(self):
        for error in (FileNotFoundError(), PermissionError(), UnicodeDecodeError(
                "utf-8", b"\xff", 0, 1, "invalid byte")):
            with self.subTest(error=error), \
                    patch.object(self.module.Path, "read_text", side_effect=error), \
                    patch.object(self.module.subprocess, "check_output", side_effect=OSError()):
                self.assertIn("b=unknown c=unknown", self.module.internal_watermark_text())


if __name__ == "__main__":
    unittest.main()
