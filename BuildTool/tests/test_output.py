import contextlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_cleanup import builder


class OutputTests(unittest.TestCase):
    def test_direct_invocation_keeps_process_ownership(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(builder.os, 'getuid', return_value=0), \
                patch.object(builder.os, 'getgid', return_value=0), \
                patch.object(builder.os, 'chown') as chown:
            output = Path(temp) / 'output.img'
            output.touch(mode=0o600)
            builder.finalize_output(output)
            chown.assert_called_once_with(output, 0, 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)

    def test_all_formats_restore_sudo_ownership_and_permissions(self):
        for format_name in ('zip', 'img', 'qcow2'):
            with self.subTest(format=format_name), tempfile.TemporaryDirectory() as temp, \
                    contextlib.ExitStack() as stack:
                output = Path(temp) / f'output.{format_name}'

                def create_artifact(path):
                    path.write_bytes(b'build output')
                    path.chmod(0o600)  # Root's restrictive umask or qemu-img mode.

                def build_disk(root, args, raw, expand):
                    create_artifact(raw)

                def run(*args):
                    self.assertEqual(args[:2], ('qemu-img', 'convert'))
                    create_artifact(args[-1])

                stack.enter_context(patch.dict(os.environ, {'SUDO_UID': '1234', 'SUDO_GID': '5678'}))
                stack.enter_context(patch.object(builder.sys, 'argv', [
                    'builder', format_name, str(output), '--_mount-namespace',
                    '--work-dir', temp, '--force-tmp']))
                stack.enter_context(patch.object(builder.os, 'geteuid', return_value=0))
                stack.enter_context(patch.object(builder.sys, 'platform', 'linux'))
                for name in ('require', 'require_cross_emulation', 'bootstrap', 'install_guest'):
                    stack.enter_context(patch.object(builder, name))
                stack.enter_context(patch.object(builder, 'mounts_under', return_value=[]))
                stack.enter_context(patch.object(builder, 'make_zip',
                                                side_effect=lambda root, path: create_artifact(path)))
                stack.enter_context(patch.object(builder, 'build_disk', side_effect=build_disk))
                stack.enter_context(patch.object(builder, 'run', side_effect=run))
                chown = stack.enter_context(patch.object(builder.os, 'chown'))

                builder.main()

                chown.assert_called_once_with(output, 1234, 5678)
                self.assertEqual(output.stat().st_mode & 0o777, 0o644)
                self.assertEqual(output.read_bytes(), b'build output')


if __name__ == '__main__':
    unittest.main()
