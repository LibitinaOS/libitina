"""Verify global appearance assets and defaults in a staged guest filesystem."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import call, patch

from test_cleanup import builder


class ThemeTests(unittest.TestCase):
    def test_global_install_and_account_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = Path(builder.__file__).resolve().parents[1]
            relative = 'Applications/Internal/Global/DeletescapeUiThemeInstall/themes'
            source = repo / relative
            shutil.copytree(source, root / 'usr/share/libitina' / relative)
            fonts = repo / 'Fonts/InclusiveSans'
            shutil.copytree(fonts, root / 'usr/share/libitina/Fonts/InclusiveSans')
            with patch.object(builder, 'run') as run:
                builder.install_desktop_appearance(root)
            for name in ('DeletescapeUi', 'DeletescapeUi-Dark'):
                for asset in (source / name).rglob('*'):
                    if asset.is_file():
                        installed = root / 'usr/share/themes' / name / asset.relative_to(source / name)
                        self.assertEqual(installed.read_bytes(), asset.read_bytes())
            font_dir = 'usr/share/fonts/truetype/inclusive-sans'
            for font in fonts.glob('*.ttf'):
                self.assertEqual((root / font_dir / font.name).read_bytes(), font.read_bytes())
            for path in ('etc/gtk-3.0/settings.ini', 'etc/skel/.config/gtk-3.0/settings.ini'):
                settings = (root / path).read_text()
                self.assertIn('gtk-theme-name=DeletescapeUi\n', settings)
                self.assertIn('gtk-application-prefer-dark-theme=false\n', settings)
            self.assertIn('gtk-font-name=Inclusive Sans 11\n',
                          (root / 'etc/gtk-3.0/settings.ini').read_text())
            override = (root / 'usr/share/glib-2.0/schemas/99_libitina.gschema.override').read_text()
            self.assertIn("gtk-theme='DeletescapeUi'", override)
            self.assertIn("font-name='Inclusive Sans 11'", override)
            self.assertEqual(run.call_args_list, [
                call('chroot', root, 'fc-cache', '/' + font_dir),
                call('chroot', root, 'glib-compile-schemas', '/usr/share/glib-2.0/schemas'),
            ])
