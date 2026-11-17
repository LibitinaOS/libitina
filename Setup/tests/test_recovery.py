"""Verify the recovery warning gates wizard creation without a display server."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_password


class RecoveryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_password.PasswordTests.setUpClass()
        cls.setup = test_password.PasswordTests.setup

    def setUp(self):
        self.gtk = Mock()
        self.gtk.ResponseType = SimpleNamespace(REJECT=-2, ACCEPT=-3)
        self.patcher = patch.object(self.setup, 'Gtk', self.gtk)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_continue_requires_explicit_selection(self):
        dialog = self.gtk.MessageDialog.return_value
        dialog.run.side_effect = [-4, -3]  # Closing/Escape must not bypass warning.
        self.assertTrue(self.setup.confirm_restart_setup())
        self.assertEqual(dialog.run.call_count, 2)
        dialog.add_button.assert_any_call('Shut Down', -2)
        dialog.add_button.assert_any_call('Continue', -3)
        self.assertEqual(self.gtk.MessageDialog.call_args.kwargs['text'],
                         'Provisioning LibitinaOS failed for an unknown reason. '
                         'You can attempt to restart setup, but it is recommended '
                         'that you reinstall LibitinaOS.')
        dialog.destroy.assert_called_once()

    def test_shutdown_does_not_continue_to_wizard(self):
        self.gtk.MessageDialog.return_value.run.return_value = -2
        with patch.object(self.setup.subprocess, 'run') as run:
            self.assertFalse(self.setup.confirm_restart_setup())
        run.assert_called_once_with(['sudo', '-n', '/usr/bin/systemctl', 'poweroff'], check=True)

    def test_shutdown_failure_keeps_warning_available(self):
        self.gtk.MessageDialog.return_value.run.side_effect = [-2, -5, -3]
        with patch.object(self.setup.subprocess, 'run', side_effect=OSError('failed')):
            self.assertTrue(self.setup.confirm_restart_setup())
        self.assertEqual(self.gtk.MessageDialog.call_count, 2)

    def test_startup_gates_wizard_on_marker_and_response(self):
        for interrupted, accepted in ((False, True), (True, True), (True, False)):
            with self.subTest(interrupted=interrupted, accepted=accepted), \
                    patch.object(self.setup.Path, 'exists', return_value=interrupted), \
                    patch.object(self.setup, 'confirm_restart_setup', return_value=accepted) as confirm, \
                    patch.object(self.setup, 'SetupWizard') as wizard:
                self.setup.main()
                self.assertEqual(confirm.call_count, int(interrupted))
                self.assertEqual(wizard.call_count, int(not interrupted or accepted))


if __name__ == '__main__':
    unittest.main()
