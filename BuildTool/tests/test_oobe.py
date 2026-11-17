"""Check recovery boot ordering and the temporary account's shutdown permission."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_cleanup import builder


class OobeTests(unittest.TestCase):
    def test_recovery_installed_before_lightdm_and_enabled(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, 'run') as run:
            root = Path(directory)
            builder.configure_oobe(root)
            service = (root / 'etc/systemd/system/libitina-oobe-recovery.service').read_text()
            self.assertIn('Before=lightdm.service\n', service)
            self.assertIn('ConditionPathExists=/var/lib/libitina-setup-in-progress\n', service)
            self.assertIn('ConditionPathExists=!/var/lib/libitina/setup-complete\n', service)
            self.assertIn('provision.py --recover-oobe\n', service)
            run.assert_any_call('chroot', root, 'systemctl', 'enable', 'libitina-oobe-recovery.service')
            sudoers = root / 'etc/sudoers.d/libitina-oobe'
            self.assertIn('/usr/bin/systemctl poweroff\n', sudoers.read_text())
            self.assertEqual(sudoers.stat().st_mode & 0o777, 0o440)


if __name__ == '__main__':
    unittest.main()
