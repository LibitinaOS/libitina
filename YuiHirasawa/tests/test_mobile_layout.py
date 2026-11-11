"""Mobile cutout configuration, persistence, and bar sizing without a compositor."""

import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings
from mobile_layout import inset_content


class MobileLayoutSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.json'
        config_patch = patch.object(settings, 'CONFIG_PATH', self.path)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def test_existing_configs_gain_defaults_and_do_not_share_mutable_values(self):
        self.path.write_text('{"shell": "desktop"}')
        loaded = settings.load_config()
        self.assertEqual(loaded['mobile_layout'], settings.MOBILE_LAYOUT_DEFAULTS)
        loaded['mobile_layout']['safe_inset_top'] = 50
        self.assertEqual(settings.load_config()['mobile_layout']['safe_inset_top'], 0)

    def test_partial_config_round_trip_preserves_shell_and_unknown_settings(self):
        settings.save_config({'shell': 'desktop', 'future': True,
                              'mobile_layout': {'safe_inset_top': 40, 'status_bar_height': 24}})
        config = settings.load_config()
        self.assertEqual(config['shell'], 'desktop')
        self.assertTrue(config['future'])
        self.assertEqual(config['mobile_layout']['safe_inset_top'], 40)
        self.assertEqual(config['mobile_layout']['status_bar_height'], 24)
        self.assertEqual(config['mobile_layout']['navigation_bar_height'], 28)
        settings.set_wallpaper_enabled(False)
        self.assertEqual(settings.load_config()['mobile_layout'], config['mobile_layout'])

    def test_invalid_values_do_not_replace_saved_config_and_load_independently(self):
        settings.save_config({})
        saved = self.path.read_bytes()
        for value in (None, [], 2, '24'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    settings.save_config({'mobile_layout': value})
                self.assertEqual(self.path.read_bytes(), saved)
                self.path.write_text(json.dumps({'mobile_layout': value}))
                self.assertEqual(settings.load_config()['mobile_layout'], settings.MOBILE_LAYOUT_DEFAULTS)
                self.path.write_bytes(saved)
        for key in settings.MOBILE_LAYOUT_DEFAULTS:
            for value in (True, -1, 513, 1.5, '20', None):
                with self.subTest(key=key, value=value):
                    layout = {'safe_inset_right': 12, key: value}
                    with self.assertRaises(ValueError):
                        settings.save_config({'mobile_layout': layout})
                    self.assertEqual(self.path.read_bytes(), saved)
                    self.path.write_text(json.dumps({'mobile_layout': layout}))
                    loaded = settings.load_config()['mobile_layout']
                    self.assertEqual(loaded[key], settings.MOBILE_LAYOUT_DEFAULTS[key])
                    if key != 'safe_inset_right':
                        self.assertEqual(loaded['safe_inset_right'], 12)
                    self.path.write_bytes(saved)


def bar_class(name, layout):
    """Load the production bar with GTK calls recorded instead of displayed."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'mobile.py').read_text())
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)

    class Window:
        def __init__(self, **kwargs):
            self.calls = Mock()

        def __getattr__(self, name):
            return getattr(self.calls, name)

    gtk = Mock(Window=Window, STYLE_PROVIDER_PRIORITY_APPLICATION=600)
    namespace = dict(Gtk=gtk, GtkLayerShell=Mock(), GLib=Mock(),
                     load_layout=lambda: layout, inset_content=inset_content,
                     MobileStatusControls=Mock(), ApplicationLauncher=Mock(),
                     LauncherControl=Mock(), RunningWindow=Mock(), KeyboardVisibilityMonitor=Mock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'mobile.py', 'exec'), namespace)
    return namespace[name], namespace


class MobileBarLayoutTests(unittest.TestCase):
    def setUp(self):
        self.layout = {**settings.MOBILE_LAYOUT_DEFAULTS,
                       'safe_inset_top': 40, 'safe_inset_bottom': 12,
                       'safe_inset_left': 8, 'safe_inset_right': 10,
                       'status_bar_height': 24, 'status_bar_padding_top': 2,
                       'status_bar_padding_bottom': 3, 'status_bar_padding_left': 4,
                       'navigation_bar_height': 36, 'navigation_bar_osk_height': 20}

    def test_status_bar_reserves_cutout_plus_content_and_padding(self):
        cls, ns = bar_class('StatusBar', self.layout)
        bar = cls()
        bar.set_size_request.assert_called_with(-1, 69)
        ns['GtkLayerShell'].auto_exclusive_zone_enable.assert_called_once_with(bar)
        frame = bar.add.call_args.args[0]
        frame.set_margin_top.assert_called_with(42)
        frame.set_margin_bottom.assert_called_with(3)
        frame.set_margin_start.assert_called_with(12)
        frame.set_margin_end.assert_called_with(10)

    def test_navigation_keeps_bottom_inset_when_keyboard_opens_and_closes(self):
        cls, ns = bar_class('Taskbar', self.layout)
        bar = cls()
        bar.set_size_request.assert_called_with(-1, 48)
        bar._bar.set_margin_bottom.assert_called_with(12)
        bar._bar.set_margin_top.assert_called_with(0)
        for visible, total, content in ((True, 32, 20), (False, 48, 36)):
            bar._set_osk_visible(visible)
            bar.set_size_request.assert_called_with(-1, total)
            bar._bar.set_size_request.assert_called_with(-1, content)
            ns['GtkLayerShell'].auto_exclusive_zone_enable.assert_called_with(bar)


if __name__ == '__main__':
    unittest.main()
