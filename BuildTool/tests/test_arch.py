import contextlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cleanup import builder


class ArchitectureTests(unittest.TestCase):
    def test_native_and_cross_detection(self):
        for host, native, foreign in [('x86_64', 'amd64', 'arm64'), ('aarch64', 'arm64', 'amd64')]:
            with patch.object(builder.platform, 'machine', return_value=host):
                self.assertFalse(builder.cross_build(native))
                self.assertTrue(builder.cross_build(foreign))

    def test_native_build_does_not_require_qemu(self):
        with patch.object(builder, 'cross_build', return_value=False), patch.object(Path, 'read_text') as read:
            builder.require_cross_emulation('arm64')
            read.assert_not_called()

    def test_cross_build_requires_enabled_fixed_interpreter(self):
        for registration, global_status, accepted in [
                ('enabled\nflags: POCF\n', 'enabled', True),
                ('enabled\nflags: POC\n', 'enabled', False),
                ('disabled\nflags: F\n', 'enabled', False),
                ('enabled\nflags: F\n', 'disabled', False)]:
            with self.subTest(registration=registration, global_status=global_status), \
                    patch.object(builder, 'cross_build', return_value=True), \
                    patch.object(Path, 'read_text', side_effect=[registration, global_status]):
                if accepted:
                    builder.require_cross_emulation('arm64')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'F flag'):
                        builder.require_cross_emulation('arm64')

    def test_foreign_bootstrap_uses_two_stages(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(builder, 'cross_build', return_value=True), \
                patch.object(builder, 'chroot_mounts', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'inhibit_services', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'install_libitina'), \
                patch.object(builder, 'run', return_value=SimpleNamespace(stdout='test\n')) as run:
            root = Path(temp)
            args = SimpleNamespace(arch='arm64', release='trixie', mirror='http://example.test',
                                   output=root / 'output.zip', hostname='guest')
            builder.bootstrap(root, args)
            calls = [call.args for call in run.call_args_list]
            first = next(call for call in calls if call[0] == 'debootstrap')
            self.assertIn('--arch=arm64', first)
            self.assertIn('--foreign', first)
            probe = ('chroot', root, '/bin/true')
            second = ('chroot', root, '/debootstrap/debootstrap', '--second-stage')
            self.assertLess(calls.index(first), calls.index(probe))
            self.assertLess(calls.index(probe), calls.index(second))

    def test_arm64_packages(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(builder, 'chroot_mounts', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'inhibit_services', return_value=contextlib.nullcontext()), \
                patch.object(builder, 'configure_oobe'), patch.object(builder, 'run') as run:
            builder.install_guest(Path(temp), SimpleNamespace(arch='arm64', root_password=None), True)
            packages = next(call.args for call in run.call_args_list if 'install' in call.args)
            self.assertIn('linux-image-arm64', packages)
            self.assertIn('grub-efi-arm64-bin', packages)
            self.assertIn('grub2-common', packages)
            self.assertNotIn('linux-image-amd64', packages)

    def test_bootloaders_use_target_architecture(self):
        for arch, target, filename in [('amd64', 'x86_64-efi', 'BOOTX64.EFI'),
                                       ('arm64', 'arm64-efi', 'BOOTAA64.EFI')]:
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as temp, \
                    patch.object(builder, 'chroot_mounts', return_value=contextlib.nullcontext()), \
                    patch.object(builder, 'run') as run:
                root = Path(temp)
                loader = root / 'boot/efi/EFI/BOOT' / filename
                loader.parent.mkdir(parents=True)
                loader.touch()
                builder.install_bootloader(root, arch)
                self.assertIn('--target=' + target, run.call_args_list[0].args)
                self.assertIn('--no-nvram', run.call_args_list[0].args)


if __name__ == '__main__':
    unittest.main()
