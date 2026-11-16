"""Verify global theme assets and defaults in a staged guest filesystem."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from test_cleanup import builder


class ThemeTests(unittest.TestCase):
    def test_global_install_and_account_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            relative = 'Applications/Internal/Global/DeletescapeUiThemeInstall/themes'
            source = Path(builder.__file__).resolve().parents[1] / relative
            shutil.copytree(source, root / 'usr/share/libitina' / relative)
            with patch.object(builder, 'run') as run:
                builder.install_gtk_themes(root)
            for name in ('DeletescapeUi', 'DeletescapeUi-Dark'):
                for asset in (source / name).rglob('*'):
                    if asset.is_file():
                        installed = root / 'usr/share/themes' / name / asset.relative_to(source / name)
                        self.assertEqual(installed.read_bytes(), asset.read_bytes())
            for path in ('etc/gtk-3.0/settings.ini', 'etc/skel/.config/gtk-3.0/settings.ini'):
                settings = (root / path).read_text()
                self.assertIn('gtk-theme-name=DeletescapeUi\n', settings)
                self.assertIn('gtk-application-prefer-dark-theme=false\n', settings)
            override = (root / 'usr/share/glib-2.0/schemas/99_libitina.gschema.override').read_text()
            self.assertIn("gtk-theme='DeletescapeUi'", override)
            run.assert_called_once_with('chroot', root, 'glib-compile-schemas',
                                        '/usr/share/glib-2.0/schemas')
