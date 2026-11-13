"""Match window identities before generic titles shared by native/Android apps."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import ApplicationCatalog


class ApplicationIconTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.entries = []
        self.catalog = ApplicationCatalog()
        self.catalog._desktop_icon_cache = {}
        files = patch.object(self.catalog, '_desktop_files', side_effect=lambda: self.entries)
        files.start()
        self.addCleanup(files.stop)

    def entry(self, desktop_id, name, icon=None, **fields):
        path = self.root / (desktop_id + '.desktop')
        values = dict(Type='Application', Name=name, **fields)
        if icon is not None:
            values['Icon'] = icon
        path.write_text('[Desktop Entry]\n' + ''.join(f'{key}={value}\n' for key, value in values.items()))
        self.entries.append(path)
        return path

    def android(self, package='com.android.settings', name='Settings'):
        return self.entry('waydroid.' + package, name, '/android/' + package + '.png',
                          Exec='waydroid app launch ' + package,
                          Categories='X-WayDroid-App;', NoDisplay='true')

    def test_native_camera_id_beats_earlier_android_title(self):
        self.android('org.lineageos.aperture', 'Camera')
        self.entry('org.gnome.Snapshot', 'Camera', 'org.gnome.Snapshot', Exec='snapshot')
        self.assertEqual(self.catalog._desktop_icon_spec('org.gnome.Snapshot', 'Camera'),
                         ('theme', 'org.gnome.Snapshot'))

    def test_settings_wmclass_beats_other_settings_entries(self):
        self.android()
        self.entry('other-settings', 'Settings', 'other-settings')
        self.entry('libitina-YuiSettings', 'Settings', 'preferences-system',
                   StartupWMClass='org.libitina.Yui.Settings')
        self.assertEqual(self.catalog._desktop_icon_spec('org.libitina.Yui.Settings', 'Settings'),
                         ('theme', 'preferences-system'))

    def test_exact_desktop_id_beats_earlier_native_title_match(self):
        self.entry('other', 'Camera', 'wrong')
        self.entry('org.gnome.Snapshot', 'Camera', 'camera')
        self.assertEqual(self.catalog._desktop_icon_spec('org.gnome.Snapshot.desktop', 'Camera'),
                         ('theme', 'camera'))

    def test_missing_icon_on_strong_match_does_not_borrow_another_apps_icon(self):
        self.entry('native', 'Settings')
        self.android()
        self.entry('other', 'Settings', 'wrong-native')
        self.assertEqual(self.catalog._desktop_icon_spec('native', 'Settings'), ('theme', 'native'))
        # The strongest match must win even if encountered last.
        self.entries.reverse()
        self.catalog._desktop_icon_cache.clear()
        self.assertEqual(self.catalog._desktop_icon_spec('native', 'Settings'), ('theme', 'native'))

    def test_android_windows_keep_their_own_icon(self):
        self.entry('native', 'Settings', 'preferences-system')
        self.android()
        for app_id in ('waydroid.com.android.settings', 'com.android.settings'):
            self.assertEqual(self.catalog._desktop_icon_spec(app_id, 'Settings'),
                             ('path', '/android/com.android.settings.png'))

    def test_title_only_fallback_ignores_waydroid(self):
        self.android()
        self.entry('native', 'Settings', 'preferences-system')
        self.assertEqual(self.catalog._desktop_icon_spec('main.py', 'Settings'),
                         ('theme', 'preferences-system'))

    def test_only_android_title_match_returns_generic_icon(self):
        self.android()
        self.assertEqual(self.catalog._desktop_icon_spec('unknown', 'Settings'), ('theme', 'unknown'))
        self.assertEqual(self.catalog._desktop_icon_spec('', 'Settings'), ('', ''))

    def test_executable_identity_matches_command_with_arguments(self):
        self.android('org.lineageos.aperture', 'Camera')
        self.entry('camera-launcher', 'Camera', 'camera-native', Exec='"/usr/bin/snapshot" --new-window %U')
        self.assertEqual(self.catalog._desktop_icon_spec('snapshot', 'Camera'), ('theme', 'camera-native'))

    def test_empty_identity_does_not_match_missing_fields(self):
        self.entry('unrelated', 'Other', 'wrong')
        self.assertEqual(self.catalog._desktop_icon_spec('', ''), ('', ''))

    def test_real_identity_wins_over_punctuation_normalization_collision(self):
        self.entry('org.exampleCamera', 'Camera', 'wrong')
        self.entry('org.example.Camera', 'Camera', 'right')
        self.assertEqual(self.catalog._desktop_icon_spec('org.example.Camera', 'Camera'), ('theme', 'right'))


if __name__ == '__main__':
    unittest.main()
