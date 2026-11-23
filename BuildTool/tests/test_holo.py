"""Exercise Holo build selection, client staging, and compositor fallback."""
from contextlib import nullcontext
import hashlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_cleanup import builder


class HoloTests(unittest.TestCase):
    def test_guest_selects_holo_setup_and_backports(self):
        for arch in ('amd64', 'arm64'):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as directory, \
                    patch.object(builder, 'chroot_mounts', return_value=nullcontext()), \
                    patch.object(builder, 'inhibit_services', return_value=nullcontext()), \
                    patch.object(builder, 'install_desktop_appearance'), \
                    patch.object(builder, 'configure_oobe') as oobe, \
                    patch.object(builder, 'configure_holo') as holo, \
                    patch.object(builder, 'run') as run:
                root = Path(directory)
                args = SimpleNamespace(arch=arch, release='trixie', mirror='http://example.test',
                                       holo=True, holo_autologin=False, root_password=None)
                builder.install_guest(root, args, True)
                oobe.assert_not_called()
                holo.assert_called_once_with(root, arch, autologin=False)
                sources = (root / 'etc/apt/sources.list').read_text()
                self.assertIn('trixie main contrib non-free-firmware\n', sources)
                self.assertIn('trixie-backports main contrib\n', sources)
                calls = [call.args for call in run.call_args_list]
                multiarch = ('chroot', root, 'dpkg', '--add-architecture', 'i386')
                if arch == 'amd64':
                    self.assertLess(calls.index(multiarch),
                                    calls.index(('chroot', root, 'apt-get', 'update')))
                else:
                    self.assertNotIn(multiarch, calls)

    def test_session_prefers_gamescope_and_falls_back_to_labwc(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(builder, 'seed_holo_steam') as seed, \
                patch.object(builder, 'run',
                             return_value=SimpleNamespace(stdout='  Candidate: 3.16.22\n')) as run:
            root = Path(directory)
            builder.configure_holo(root, 'amd64')
            seed.assert_called_once_with(root, 'amd64')
            self.assertIn('gamescope', run.call_args_list[1].args)
            run.assert_any_call('chroot', root, 'useradd', '--create-home', '--user-group',
                                '--shell', '/bin/bash', 'steamos')
            run.assert_any_call('chroot', root, 'chpasswd', input='steamos:gaben\n')
            sshd = (root / 'etc/ssh/sshd_config.d/90-libitina-holo.conf').read_text()
            self.assertIn('Match User steamos\n    AuthenticationMethods publickey\nMatch all\n', sshd)
            lightdm = (root / 'etc/lightdm/lightdm.conf.d/90-libitina-holo.conf').read_text()
            self.assertIn('user-session=libitina-holo\n', lightdm)
            self.assertNotIn('autologin-user', lightdm)
            builder.install_holo_sessions(root, gamescope_available=True, autologin=True)
            lightdm = (root / 'etc/lightdm/lightdm.conf.d/90-libitina-holo.conf').read_text()
            self.assertIn('autologin-user=steamos\n', lightdm)
            self.assertIn('autologin-session=libitina-holo\n', lightdm)
            session = root / 'usr/local/bin/libitina-holo-session'
            bin_dir = session.parent
            labwc = bin_dir / 'labwc'
            labwc.write_text('#!/bin/sh\nprintf "labwc %s %s\\n" "$XDG_CURRENT_DESKTOP" "$*"\n')
            labwc.chmod(0o755)
            gamescope = bin_dir / 'gamescope'
            gamescope.write_text('#!/bin/sh\nprintf "gamescope %s %s\\n" "$XDG_CURRENT_DESKTOP" "$*"\n'
                                 'exit "$GAMESCOPE_STATUS"\n')
            gamescope.chmod(0o755)
            home = root / 'home/steamos'
            log_dir = home / '.local/state/libitina/holo'
            session_dir = root / 'usr/share/wayland-sessions'

            def launch(profile, gamescope_status):
                entry = (session_dir / f'{profile}.desktop').read_text()
                command = next(line for line in entry.splitlines() if line.startswith('Exec='))
                args = command.split()[1:]
                env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(home),
                       'GAMESCOPE_STATUS': gamescope_status}
                result = subprocess.run([session, *args], env=env)
                return result.returncode, (log_dir / f'{profile}.log').read_text()

            flags = ('/usr/local/bin/libitina-steam -steamdeck -steamos3 -gamepadui '
                     '-clientbeta steamdeck_publicbeta')
            for status in ('0', '1'):
                result, log = launch('libitina-holo', status)
                self.assertEqual(result, 0)
                self.assertIn(f'gamescope gamescope -e -- {flags}', log)
                self.assertEqual(f'labwc labwc --startup {flags}' in log, status == '1')
            previous = (log_dir / 'libitina-holo.log.previous').read_text()
            self.assertIn('gamescope gamescope -e --', previous)
            self.assertNotIn('labwc labwc --startup', previous)

            profiles = {
                'default': '',
                'force-gpu': ' -cef-force-gpu',
                'x11': ' -cef-ozone-platform=x11',
                'no-sandbox': ' -no-cef-sandbox',
                'angle': ' -cef-use-gl=angle -cef-use-angle=vulkan',
                'combined': ' -cef-force-gpu -cef-ozone-platform=x11 -cef-use-gl=angle '
                            '-cef-use-angle=vulkan -no-cef-sandbox',
            }
            for compositor in ('gamescope', 'labwc'):
                for profile, overrides in profiles.items():
                    with self.subTest(compositor=compositor, profile=profile):
                        result, log = launch(f'libitina-holo-{compositor}-{profile}', '1')
                        if compositor == 'gamescope':
                            self.assertEqual(result, 1)
                            self.assertIn(f'gamescope gamescope -e -- {flags}{overrides}\n', log)
                            self.assertNotIn('labwc labwc', log)
                        else:
                            self.assertEqual(result, 0)
                            self.assertIn(f'labwc labwc --startup {flags}{overrides}\n', log)
                            self.assertNotIn('gamescope gamescope', log)
            result, log = launch('libitina-holo-terminal', '0')
            self.assertEqual(result, 0)
            self.assertIn('labwc labwc --session /usr/bin/foot', log)
            self.assertNotIn('libitina-steam', log)

    def test_arm64_client_is_configured_after_verified_extraction(self):
        archive = b'steam archive'
        checksum = hashlib.sha256(archive).hexdigest()
        packages = ('steam_linuxarm64', 'bins_linuxarm64_linuxarm64', 'steamui_websrc_all',
                    'bins_steamrt_linuxarm64')
        filenames = [f'{package}.zip.{"a" * 40}' for package in packages]
        manifest = '\n'.join(f'"{package}" {{ "file" "{filename}" "sha2" "{checksum}" }}'
                             for package, filename in zip(packages, filenames))
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'tmp').mkdir()
                home = root / 'home/steamos'
                steam = home / '.local/share/Steam'
                links = home / '.steam'
                links.mkdir(parents=True)
                helpers = ('steam', 'steamwebhelper', 'steamwebhelper.sh',
                           'gldriverquery', 'vulkandriverquery', 'steamsysinfo')

                def stage_client(*args, **kwargs):
                    command = args[6] if args[2] == 'runuser' else None
                    if command == 'curl' and args[-1].endswith('/steam_client_steamdeck_publicbeta_linuxarm64'):
                        return SimpleNamespace(stdout=manifest)
                    if command == 'curl':
                        download = root / args[args.index('-o') + 1].lstrip('/')
                        download.write_bytes(archive if valid else b'corrupt')
                    if command == 'bsdtar' and Path(args[8]).name == filenames[1]:
                        runtime = steam / 'steamrtarm64'
                        runtime.mkdir(parents=True)
                        (steam / 'linuxarm64').mkdir()
                        for helper in helpers:
                            (runtime / helper).touch(mode=0o644)
                    if command == 'sh':
                        subprocess.run([*args[6:10], steam, links], check=True)
                    if command == 'mkdir':
                        for path in args[8:]:
                            (root / path.lstrip('/')).mkdir(parents=True, exist_ok=True)
                    if command == 'tee':
                        (root / args[-1].lstrip('/')).write_text(kwargs['input'])

                with patch.object(builder, 'run', side_effect=stage_client) as run:
                    if valid:
                        builder.seed_holo_steam(root, 'arm64')
                        extractions = [call.args for call in run.call_args_list if 'bsdtar' in call.args]
                        self.assertEqual([Path(args[8]).name for args in extractions], filenames[:3])
                        self.assertTrue(all(args[2:6] == ('runuser', '-u', 'steamos', '--')
                                            for args in extractions))
                        for helper in helpers:
                            self.assertTrue((steam / 'steamrtarm64' / helper).stat().st_mode & 0o100)
                        self.assertEqual((steam / 'package/beta').read_text(), 'steamdeck_publicbeta\n')
                        self.assertFalse((steam / 'steamrt64').is_symlink())
                        self.assertEqual((links / 'sdkarm64').resolve(), steam / 'linuxarm64')
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                            builder.seed_holo_steam(root, 'arm64')
                        self.assertFalse(any('bsdtar' in call.args for call in run.call_args_list))

    def test_arm64_launcher_restarts_only_for_steam_update(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(builder, 'seed_holo_steam'), \
                patch.object(builder, 'run',
                             return_value=SimpleNamespace(stdout='  Candidate: 3.16.22\n')):
            root = Path(directory)
            builder.configure_holo(root, 'arm64')
            launcher = root / 'usr/local/bin/libitina-steam'
            flags = ['-gamepadui', '-clientbeta', 'steamdeck_publicbeta']
            for first_exit, later_exit, expected_exit, attempts in (
                    (42, 0, 0, 2), (42, 42, 42, 3), (7, 0, 7, 1)):
                with self.subTest(first_exit=first_exit, later_exit=later_exit):
                    home = root / f'home-{first_exit}-{later_exit}'
                    runtime = home / '.local/share/Steam/steamrtarm64'
                    runtime.mkdir(parents=True)
                    steam = runtime / 'steam'
                    steam.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/invocations"
if [ ! -f "$HOME/started" ]; then
    : > "$HOME/started"
    exit "$FIRST_EXIT"
fi
exit "$LATER_EXIT"
''')
                    steam.chmod(0o755)
                    result = subprocess.run([launcher, *flags], capture_output=True, text=True,
                                            env={'HOME': str(home), 'FIRST_EXIT': str(first_exit),
                                                 'LATER_EXIT': str(later_exit)}, timeout=5)
                    self.assertEqual(result.returncode, expected_exit)
                    self.assertEqual((home / 'invocations').read_text().splitlines(),
                                     [' '.join(flags)] * attempts)
