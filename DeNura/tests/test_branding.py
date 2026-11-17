import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('denura', Path(__file__).parents[1] / 'main.py')
denura = importlib.util.module_from_spec(spec)
spec.loader.exec_module(denura)


class BrandingTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.os_release = self.root / 'os-release'
        self.original_release = ('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'
                                 'NAME="Debian GNU/Linux"\nVERSION_ID="13"\nID=debian\n')
        self.os_release.write_text(self.original_release)
        self.banners = {self.root / path.name: text
                        for path, text in denura.CONSOLE_BANNERS.items()}
        self.original_banners = {
            self.root / 'issue': 'Debian GNU/Linux 13 \\n \\l\n\n',
            self.root / 'issue.net': 'Debian GNU/Linux 13\n',
        }
        for path, text in self.original_banners.items():
            path.write_text(text)
        self.backup = self.root / 'os-release-backup'
        self.enterContext(patch.object(denura, 'OS_RELEASE', self.os_release))
        self.enterContext(patch.object(denura, 'BACKUP', self.backup))
        self.enterContext(patch.object(denura, 'CONSOLE_BANNERS', self.banners))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_rebrand_updates_banners_and_backs_up_originals(self):
        denura.rebrand()
        self.assertIn('NAME="LibitinaOS"', self.os_release.read_text())
        self.assertEqual(self.backup.read_text(), self.original_release)
        for path, text in self.banners.items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(), text.format(pretty_name='LibitinaOS (Debian 13)'))
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                backup = denura.banner_backup_path(path, self.backup)
                self.assertEqual(backup.read_text(), self.original_banners[path])
        self.assertEqual((self.root / 'issue').read_text(), 'LibitinaOS (Debian 13) \\n \\l\n\n')
        self.assertEqual((self.root / 'issue.net').read_text(), 'LibitinaOS (Debian 13)\n')
        self.assertEqual(denura.parse_os_release(self.os_release)['PRETTY_NAME'],
                         'LibitinaOS (Debian 13)')

    def test_revert_restores_release_and_original_banners(self):
        denura.rebrand()
        denura.revert()
        self.assertEqual(self.os_release.read_text(), self.original_release)
        for path, text in self.original_banners.items():
            self.assertEqual(path.read_text(), text)

    def test_repeated_rebrand_pairs_banners_with_latest_backup(self):
        denura.rebrand()
        os.utime(self.backup, (1, 1))
        latest_release = self.os_release.read_text()
        custom_banner = 'Custom console greeting \\n \\l\n'
        issue = self.root / 'issue'
        issue.write_text(custom_banner)
        denura.rebrand()
        latest = denura.find_latest_backup()
        self.assertNotEqual(latest, self.backup)
        self.assertEqual(denura.banner_backup_path(issue, latest).read_text(), custom_banner)
        denura.revert()
        self.assertEqual(self.os_release.read_text(), latest_release)
        self.assertEqual(issue.read_text(), custom_banner)

    def test_revert_accepts_older_backups_without_banners(self):
        self.backup.write_text(self.original_release)
        denura.revert()
        for path, text in self.original_banners.items():
            self.assertEqual(path.read_text(), text)

    def test_missing_banner_remains_absent(self):
        issue_net = self.root / 'issue.net'
        issue_net.unlink()
        denura.rebrand()
        denura.revert()
        self.assertFalse(issue_net.exists())


if __name__ == '__main__':
    unittest.main()
