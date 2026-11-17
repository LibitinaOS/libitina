"""Check OOBE backgrounds and session cleanup without a Wayland compositor."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET


SETUP_DIR = Path(__file__).resolve().parents[1]


def background_module():
    gi = ModuleType('gi')
    gi.require_version = Mock()
    repository = ModuleType('gi.repository')
    for name in ('Gdk', 'GLib', 'Gtk', 'GtkLayerShell'):
        setattr(repository, name, Mock())
    spec = importlib.util.spec_from_file_location('setup_background_test', SETUP_DIR / 'wallpaper.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}):
        spec.loader.exec_module(module)
    return module


class BackgroundTests(unittest.TestCase):
    def test_current_identity_warnings_precede_watermark_with_blank_line(self):
        module = background_module()
        for hostname, username, warnings in (
                ('FuckDavidBaszucki', 'defaultuser0', []),
                ('other-host', 'defaultuser0', [
                    'Warning: Hostname is other-host (expected FuckDavidBaszucki).']),
                ('FuckDavidBaszucki', 'other-user', [
                    'Warning: Username is other-user (expected defaultuser0).']),
                ('other-host', 'other-user', [
                    'Warning: Hostname is other-host (expected FuckDavidBaszucki).',
                    'Warning: Username is other-user (expected defaultuser0).']),
                ('FuckDavidBaszucki', 'dapixelprowler', [
                    'Warning: Username is dapixelprowler (expected defaultuser0).'])):
            with self.subTest(hostname=hostname, username=username), \
                    patch.object(module.socket, 'gethostname', return_value=hostname), \
                    patch.object(module.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name=username)), \
                    patch.object(module.Path, 'read_text', return_value=json.dumps({
                        'builder_hostname': 'unrelated-host', 'builder_username': 'unrelated-user'})), \
                    patch.object(module.subprocess, 'check_output', side_effect=['branch', 'commit']), \
                    patch.object(module.platform, 'freedesktop_os_release',
                                 return_value={'PRETTY_NAME': 'Test OS'}), \
                    patch.object(module.platform, 'release', return_value='kernel'):
                watermark = 'Test OS\nkernel\nb=branch c=commit\nI <3 Jamie Paige'
                expected = '\n'.join(warnings) + '\n\n' + watermark if warnings else watermark
                self.assertEqual(module.internal_watermark_text(), expected)

    def test_failed_current_identity_lookups_show_unknown(self):
        module = background_module()
        with patch.object(module.socket, 'gethostname', side_effect=OSError()), \
                patch.object(module.pwd, 'getpwuid', side_effect=KeyError()), \
                patch.object(module.Path, 'read_text', return_value='{}'), \
                patch.object(module.subprocess, 'check_output', side_effect=['branch', 'commit']):
            text = module.internal_watermark_text()
        self.assertTrue(text.startswith(
            'Warning: Hostname is unknown (expected FuckDavidBaszucki).\n'
            'Warning: Username is unknown (expected defaultuser0).\n\n'))

    def test_background_surfaces_hotplug_and_cleanup(self):
        module = background_module()
        display = module.Gdk.Display.get_default.return_value
        first, second = object(), object()
        monitors = [first]
        display.get_n_monitors.side_effect = lambda: len(monitors)
        display.get_monitor.side_effect = lambda i: monitors[i]
        display.connect.side_effect = [11, 12]
        windows = [Mock(), Mock()]
        module.Gtk.Window.side_effect = windows
        with patch.object(module, 'internal_watermark_text', return_value='build info'):
            background = module.SetupBackground()
        window = windows[0]
        module.GtkLayerShell.set_layer.assert_called_with(
            window, module.GtkLayerShell.Layer.BACKGROUND)
        module.GtkLayerShell.set_keyboard_mode.assert_called_with(
            window, module.GtkLayerShell.KeyboardMode.NONE)
        self.assertEqual(module.GtkLayerShell.set_anchor.call_count, 4)
        window.set_accept_focus.assert_called_once_with(False)
        callback = window.connect.call_args.args[1]
        self.assertTrue(callback(window, object()))
        module.Gtk.Label.assert_called_once_with(label='build info', xalign=1)
        background._monitors_changed()
        self.assertEqual(module.Gtk.Window.call_count, 1)
        monitors.append(second)
        background._monitors_changed()
        self.assertEqual(module.Gtk.Window.call_count, 2)
        monitors.remove(first)
        background._monitors_changed()
        window.destroy.assert_called_once()
        background.close()
        windows[1].destroy.assert_called_once()
        self.assertFalse(background._windows)
        self.assertEqual(display.disconnect.call_count, 2)

    def test_git_precedence_and_buildtool_fallback(self):
        module = background_module()
        for results, expected in (
                (['live', 'commit'], 'b=live c=commit'),
                ([OSError(), OSError()], 'b=built c=saved'),
                (['live', OSError()], 'b=live c=saved')):
            with self.subTest(expected=expected), \
                    patch.object(module.subprocess, 'check_output', side_effect=results), \
                    patch.object(module.Path, 'read_text', return_value=(
                        '{"git_branch":"built","git_commit":"saved"}')):
                self.assertIn(expected, module.internal_watermark_text())

    def test_unavailable_or_malformed_metadata(self):
        module = background_module()
        for data in ('bad json', '[]', '{"git_branch":null,"git_commit":42}'):
            with self.subTest(data=data), \
                    patch.object(module.subprocess, 'check_output', side_effect=OSError()), \
                    patch.object(module.Path, 'read_text', return_value=data):
                self.assertIn('b=unknown c=unknown', module.internal_watermark_text())
        with patch.object(module.subprocess, 'check_output', side_effect=OSError()), \
                patch.object(module.Path, 'read_text', side_effect=FileNotFoundError()):
            self.assertIn('b=unknown c=unknown', module.internal_watermark_text())


class SessionTests(unittest.TestCase):
    def test_right_click_binding_is_disabled(self):
        root = ET.parse(SETUP_DIR / 'labwc/rc.xml').getroot()
        binding = root.find('mouse/context[@name="Root"]/mousebind[@button="Right"]')
        self.assertIsNotNone(binding)
        self.assertEqual(binding.get('action'), 'Press')
        self.assertEqual(list(binding), [])
        session = (SETUP_DIR / 'oobe-session.sh').read_text()
        self.assertIn('--config /usr/share/libitina/Setup/labwc', session)
        self.assertIn('--session \'/bin/sh /usr/share/libitina/Setup/oobe-client.sh\'', session)

    def test_background_is_reaped_when_wizard_exits_or_fails(self):
        for status in (0, 7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                client = directory / 'oobe-client.sh'
                client.write_text((SETUP_DIR / 'oobe-client.sh').read_text())
                (directory / 'wallpaper.py').write_text('''import os
from pathlib import Path
import signal
signal.signal(signal.SIGTERM, lambda *_: exit(0))
Path(__file__).with_name('pid').write_text(str(os.getpid()))
while True:
    signal.pause()
''')
                (directory / 'main.py').write_text('''from pathlib import Path
import time
deadline = time.monotonic() + 5
while not Path(__file__).with_name('pid').exists():
    if time.monotonic() > deadline:
        raise SystemExit(99)
    time.sleep(0.01)
raise SystemExit(''' + str(status) + ''')
''')
                result = subprocess.run(['sh', str(client)], timeout=10, capture_output=True)
                self.assertEqual(result.returncode, status, result.stderr)
                pid = int((directory / 'pid').read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


if __name__ == '__main__':
    unittest.main()
