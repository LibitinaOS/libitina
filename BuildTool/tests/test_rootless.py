import contextlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cleanup import builder


class RootlessTests(unittest.TestCase):
    def test_rejects_zip_before_starting_the_build(self):
        result = subprocess.run(
            [builder.sys.executable, builder.__file__, 'zip', 'output.zip', '--rootless'],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('--rootless requires an img or qcow2 disk image', result.stderr)

    def test_disk_keeps_factory_home_on_the_read_only_system_partition(self):
        for rootless in (False, True):
            for recovery in (False, True):
                with self.subTest(rootless=rootless, recovery=recovery), \
                        tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
                    root = Path(temp) / 'root'
                    raw = Path(temp) / 'disk.img'
                    loop = Path(temp) / 'loop0'
                    home_number = 4 if recovery else 3
                    for number in range(1, home_number + 1):
                        Path(f'{loop}p{number}').touch()

                    def run(*command, **kwargs):
                        if command[0] == 'losetup':
                            return SimpleNamespace(stdout=f'{loop}\n')
                        if command[0] == 'blkid':
                            return SimpleNamespace(stdout=(
                                'efi-uuid\n' if command[-1] == Path(f'{loop}p1') else 'home-uuid\n'))

                    for name in ('create_raw', 'bootstrap', 'configure_system', 'install_guest',
                                 'build_recovery', 'install_recovery', 'install_bootloader'):
                        stack.enter_context(patch.object(builder, name))
                    commands = stack.enter_context(patch.object(builder, 'run', side_effect=run))
                    mounted = stack.enter_context(patch.object(
                        builder, 'mounted', return_value=contextlib.nullcontext()))
                    install_rootless = stack.enter_context(patch.object(builder, 'install_rootless'))
                    expand = stack.enter_context(patch.object(builder, 'install_expand_service'))
                    args = SimpleNamespace(size=16, root_size=10, arch='amd64',
                                           rootless=rootless, recovery=recovery)

                    builder.build_disk(root, args, raw, expand=True)

                    home_partition = Path(f'{loop}p{home_number}')
                    if rootless:
                        self.assertNotIn((home_partition, root / 'home'),
                                         [call.args for call in mounted.call_args_list])
                        install_rootless.assert_called_once_with(root, 'home-uuid', 'efi-uuid')
                        commands.assert_any_call('sfdisk', '--part-type', raw, home_number,
                                                 builder.ROOTLESS_HOME_TYPE)
                        expand.assert_called_once_with(root, home_number,
                                                       '/dev/disk/by-partuuid/home-uuid')
                    else:
                        install_rootless.assert_not_called()
                        mounted.assert_any_call(home_partition, root / 'home')
                        self.assertNotIn('sfdisk', [call.args[0] for call in commands.call_args_list])
                        expand.assert_called_once_with(root, home_number, '/dev/disk/by-label/DEBHOME')

    def test_factory_reset_recreates_the_partition_expected_by_initramfs(self):
        home_uuid = 'fd054780-d110-4d22-a720-cef5cf3bf99a'
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(builder, 'chroot_mounts', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'run') as run:
            root = Path(temp)
            builder.install_rootless(root, home_uuid, '45dede80-51d4-41ad-8352-c7a6dafdfa2d')
            partition = (root / 'usr/lib/libitina/repart.d/10-home.conf').read_text()
            script = (root / 'etc/initramfs-tools/scripts/libitina-rootless').read_text()
            self.assertIn(f'Type={builder.ROOTLESS_HOME_TYPE}\n', partition)
            self.assertIn(f'UUID={home_uuid}\n', partition)
            self.assertIn('Format=ext4\nFactoryReset=yes\n', partition)
            self.assertIn(f'PARTUUID={home_uuid}', script)
            run.assert_called_once_with('chroot', root, 'update-initramfs', '-u', '-k', 'all')
