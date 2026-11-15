import contextlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('builder', Path(__file__).parents[1] / 'main.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CleanupTests(unittest.TestCase):
    def test_yui_installer_uses_clean_guest_environment(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(builder, 'chroot_mounts', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'inhibit_services', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'configure_oobe'), patch.object(builder, 'run') as run:
            root = Path(temp)
            builder.install_guest(root, SimpleNamespace(root_password=None), False)
            installer_calls = [call.args for call in run.call_args_list
                               if '/usr/share/libitina/YuiHirasawa/install-debian.sh' in call.args]
            self.assertEqual(installer_calls, [(
                'chroot', root, 'env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                'HOME=/root', 'USER=root', 'LOGNAME=root',
                'DEBIAN_FRONTEND=noninteractive', 'bash',
                '/usr/share/libitina/YuiHirasawa/install-debian.sh')])

    def test_all_unmounts_attempted_preserving_build_error(self):
        with patch.object(builder, 'run', side_effect=subprocess.CalledProcessError(1, 'umount')) as run:
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                try:
                    raise RuntimeError('original failure')
                finally:
                    builder.unmount_all(['sys', 'proc', 'dev'])
            self.assertEqual(run.call_count, 3)

    def test_unmount_failure_without_build_error_is_reported(self):
        with patch.object(builder, 'run', side_effect=subprocess.CalledProcessError(1, 'umount')):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.unmount_all(['dev'])

    def test_policy_restored_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            builder.write(root, '/usr/sbin/policy-rc.d', 'original', 0o750)
            with self.assertRaises(ValueError):
                with builder.inhibit_services(root):
                    self.assertIn('exit 101', (root / 'usr/sbin/policy-rc.d').read_text())
                    raise ValueError()
            policy = root / 'usr/sbin/policy-rc.d'
            self.assertEqual(policy.read_text(), 'original')
            self.assertEqual(policy.stat().st_mode & 0o777, 0o750)

    def test_mounted_directory_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as outer:
            temp = Path(outer) / 'build'
            temp.mkdir()
            with patch.object(builder.tempfile, 'mkdtemp', return_value=str(temp)), \
                    patch.object(Path, 'read_text', return_value=f'1 2 3 / {temp}/root/dev rw - tmpfs tmpfs rw\n'), \
                    patch.object(builder.shutil, 'rmtree') as remove:
                with builder.build_directory():
                    pass
                remove.assert_not_called()

    def test_tmp_rejected_before_filesystem_probe(self):
        with patch.object(builder, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Refusing build storage under /tmp'):
                builder.check_build_storage(Path('/tmp/build'))
            run.assert_not_called()

    def test_ram_filesystems_rejected(self):
        for filesystem in ('tmpfs', 'ramfs', ''):
            with self.subTest(filesystem=filesystem), patch.object(
                    builder, 'run', return_value=subprocess.CompletedProcess([], 0, filesystem)):
                with self.assertRaises(RuntimeError):
                    builder.check_build_storage(Path('/var/lib/build'))

    def test_disk_filesystem_accepted(self):
        with patch.object(builder, 'run', return_value=subprocess.CompletedProcess([], 0, 'ext4\n')):
            builder.check_build_storage(Path('/var/lib/build'))

    def test_force_tmp_explicitly_bypasses_restriction(self):
        with patch.object(builder, 'run') as run:
            builder.check_build_storage(Path('/tmp/build'), force_tmp=True)
            run.assert_not_called()

    def test_symlink_into_tmp_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'alias'
            link.symlink_to('/tmp', target_is_directory=True)
            with self.assertRaises(RuntimeError):
                builder.check_build_storage(link)

    def test_dev_bind_is_made_slave_and_all_mounts_removed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(builder, 'run') as run:
            root = Path(temp)
            with builder.chroot_mounts(root):
                pass
            calls = [call.args for call in run.call_args_list]
            self.assertIn(('mount', '--make-rslave', root / 'dev'), calls)
            self.assertEqual(calls[-3:], [('umount', '-R', root / name) for name in ('sys', 'proc', 'dev')])


if __name__ == '__main__':
    unittest.main()
