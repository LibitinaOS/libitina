#!/usr/bin/env python3
"""Build Debian rootfs ZIPs, expandable raw images, or sparse 20-GiB QCOW2 disks.

Linux amd64/arm64 host, root privileges, Debian amd64/arm64 guest, UEFI boot.
Cross builds require QEMU user emulation registered with binfmt_misc (F flag).
Host requires: debootstrap, coreutils, mount, util-linux, parted, udev,
               e2fsprogs, dosfstools, qemu-utils (for qcow2), git, python3.
Guest packages (including GRUB) are installed automatically.

WARNING: Builds use loop devices and mount namespaces; run on a trusted Linux host.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import getpass
import json
import os
import platform
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile

GIB = 1024 ** 3
MIB = 1024 ** 2
TARGETS = {
    'amd64': {'grub': 'x86_64-efi', 'efi': 'BOOTX64.EFI', 'binfmt': 'qemu-x86_64'},
    'arm64': {'grub': 'arm64-efi', 'efi': 'BOOTAA64.EFI', 'binfmt': 'qemu-aarch64'},
}


def cross_build(arch):
    host = {'x86_64': 'amd64', 'amd64': 'amd64',
            'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine().lower())
    if host is None:
        raise RuntimeError(f'Unsupported build host architecture: {platform.machine()}')
    return host != arch


def require_cross_emulation(arch):
    if not cross_build(arch):
        return
    registration = Path('/proc/sys/fs/binfmt_misc') / TARGETS[arch]['binfmt']
    try:
        status = registration.read_text()
        enabled = (registration.parent / 'status').read_text().strip() == 'enabled'
    except OSError:
        status, enabled = '', False
    flags = next((line.partition(':')[2].strip() for line in status.splitlines()
                  if line.startswith('flags:')), '')
    if not enabled or not status.startswith('enabled\n') or 'F' not in flags:
        raise RuntimeError(
            f'Cross-building {arch} requires an enabled {registration.name} binfmt handler '
            'with the F flag for chroot execution. On Debian Trixie install '
            'qemu-user and qemu-user-binfmt, then restart systemd-binfmt. '
            'On older Debian releases use qemu-user-static and binfmt-support.')
    print(f'Cross-building {arch} using {registration.name}', flush=True)


def run(*args, capture=False, **kw):
    print('+', ' '.join(map(str, args)), flush=True)
    environment = dict(kw.pop('env', os.environ))
    environment.update(LANG='C', LC_ALL='C')
    if args and str(args[0]) == 'chroot':
        environment = {
            'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME': '/root', 'USER': 'root', 'LOGNAME': 'root',
            'LANG': 'C', 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive',
        }
    kw['env'] = environment
    return subprocess.run(list(map(str, args)), check=True,
                          text=True, capture_output=capture, **kw)


def require(cmd):
    if not shutil.which(cmd):
        raise RuntimeError(f'Missing command: {cmd}')


def write(root, path, content, mode=None):
    target = root / path.lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    if mode is not None:
        target.chmod(mode)


def install_libitina(root, output):
    source = Path(__file__).resolve().parent.parent
    root = root.resolve()
    output = output.resolve()

    def ignore(directory, names):
        # Avoid copying build artifacts or recursing into a rootfs inside the repo.
        return [name for name in names
                if name in {'.git', '__pycache__'}
                or Path(directory) / name == output
                or Path(directory) / name == root
                or Path(directory) / name in root.parents]

    shutil.copytree(source, root / 'usr/share/libitina', symlinks=True,
                    ignore=ignore, dirs_exist_ok=True)


def bootstrap(root, args):
    # Resolve the source repository independently of the caller's working directory.
    source = Path(__file__).resolve().parent
    metadata = {
        'target_architecture': args.arch,
        'builder_username': os.environ.get('SUDO_USER') or getpass.getuser(),
        'builder_hostname': socket.gethostname(),
        'build_datetime': datetime.now(timezone.utc).isoformat(),
        'git_commit': run('git', '-C', source, 'rev-parse', 'HEAD',
                          capture=True).stdout.strip(),
        'git_branch': run('git', '-C', source, 'rev-parse', '--abbrev-ref', 'HEAD',
                          capture=True).stdout.strip(),
    }
    foreign = cross_build(args.arch)
    options = ['--foreign'] if foreign else []
    run('debootstrap', f'--arch={args.arch}', '--variant=minbase', *options,
        args.release, root, args.mirror)
    if foreign:
        # F-flag binfmt emulates every guest executable, including children of
        # maintainer scripts, without placing a host emulator in the image.
        try:
            run('chroot', root, '/bin/true')
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('QEMU binfmt could not execute the target /bin/true') from exc
        with chroot_mounts(root), inhibit_services(root):
            run('chroot', root, '/debootstrap/debootstrap', '--second-stage')
    install_libitina(root, args.output)
    write(root, '/etc/libitina-info', json.dumps(metadata, indent=2) + '\n', 0o644)
    write(root, '/etc/hostname', args.hostname + '\n')
    write(root, '/etc/hosts', f'127.0.0.1 localhost\n127.0.1.1 {args.hostname}\n')
    write(root, '/etc/apt/sources.list',
          f'deb {args.mirror} {args.release} main\n'
          f'deb {args.mirror} {args.release}-updates main\n'
          f'deb http://security.debian.org/debian-security {args.release}-security main\n')
    # NetworkManager manages guest networking; do not configure ifupdown.

    write(root, '/etc/resolv.conf', 'nameserver 1.1.1.1\n')
    write(root, '/etc/fstab',
          'LABEL=DEBROOT / ext4 defaults 0 1\n'
          'LABEL=DEBHOME /home ext4 defaults,nofail 0 2\n'
          'LABEL=DEBEFI /boot/efi vfat umask=0077 0 1\n')
    (root / 'home').mkdir(exist_ok=True)
    (root / 'boot/efi').mkdir(parents=True, exist_ok=True)


def mounts_under(destination):
    destination = Path(destination).resolve()
    matches = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        path = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
        if path == destination or destination in path.parents:
            matches.append(path)
    return matches


def unmount_all(destinations):
    # Attempt every unmount and preserve the original build error during unwind.
    unwinding = sys.exc_info()[0] is not None
    first_error = None
    for destination in destinations:
        # Debootstrap may have already removed mounts it found during stage two.
        if not mounts_under(destination):
            continue
        try:
            run('umount', '-R', destination)
        except subprocess.CalledProcessError as exc:
            print(f'WARNING: failed to unmount {destination}', file=sys.stderr)
            first_error = first_error or exc
    if first_error and not unwinding:
        raise first_error


def check_build_storage(path, force_tmp=False):
    path = path.resolve()
    if force_tmp:
        return
    tmp_root = Path('/tmp').resolve()
    if path == tmp_root or tmp_root in path.parents:
        raise RuntimeError(f'Refusing build storage under /tmp: {path}; '
                           'choose --work-dir on disk or explicitly pass --force-tmp')
    filesystem = run('findmnt', '--noheadings', '--output', 'FSTYPE',
                     '--target', path, capture=True).stdout.strip()
    if not filesystem or filesystem in {'tmpfs', 'ramfs'}:
        raise RuntimeError(f'Refusing build storage on {filesystem or "unknown filesystem"}: '
                           f'{path}; choose disk storage or explicitly pass --force-tmp')


@contextlib.contextmanager
def build_directory(parent=None):
    # Never let shutil recursively delete a still-mounted guest (including /dev).
    temp = Path(tempfile.mkdtemp(prefix='debian-builder-', dir=parent))
    try:
        yield temp
    finally:
        mounted_paths = mounts_under(temp)
        if mounted_paths:
            print(f'WARNING: preserving {temp}: filesystems remain mounted: '
                  + ', '.join(map(str, mounted_paths)), file=sys.stderr)
        else:
            shutil.rmtree(temp)


@contextlib.contextmanager
def inhibit_services(root):
    policy = root / 'usr/sbin/policy-rc.d'
    # Preserve a pre-existing policy, including its permissions or symlink.
    backup = policy.with_name('policy-rc.d.libitina-backup')
    existed = policy.exists() or policy.is_symlink()
    if existed:
        policy.rename(backup)
    try:
        write(root, '/usr/sbin/policy-rc.d', '#!/bin/sh\nexit 101\n', 0o755)
        yield
    finally:
        policy.unlink(missing_ok=True)
        if existed:
            backup.rename(policy)


@contextlib.contextmanager
def mounted(source, destination, *options):
    destination.mkdir(parents=True, exist_ok=True)
    run('mount', *options, source, destination)
    try:
        yield
    finally:
        unmount_all([destination])


@contextlib.contextmanager
def chroot_mounts(root):
    # /dev bind, proc and sysfs are required by package maintainer scripts.
    mounts = []
    try:
        for src, dst, opts in [('/dev', 'dev', ['--rbind']),
                               ('proc', 'proc', ['-t', 'proc']),
                               ('sysfs', 'sys', ['-t', 'sysfs'])]:
            dest = root / dst
            dest.mkdir(exist_ok=True)
            run('mount', *opts, src, dest)
            mounts.append(dest)
            if src == '/dev':
                run('mount', '--make-rslave', dest)
        yield
    finally:
        unmount_all(reversed(mounts))


def install_guest(root, args, bootable):
    packages = ['systemd-sysv', 'libpam-systemd', 'tzdata', 'dbus', 'ca-certificates', 'iproute2',
                'network-manager', 'sudo', 'openssh-server', 'lightdm',
                'lightdm-gtk-greeter', 'xserver-xorg', 'locales', 'keyboard-configuration', 'zstd',
                'gsettings-desktop-schemas', 'libglib2.0-bin', 'dconf-gsettings-backend',
                'desktop-file-utils', 'fontconfig']
    if bootable:
        packages += [f'linux-image-{args.arch}', f'grub-efi-{args.arch}-bin',
                     'grub-common', 'grub2-common', 'e2fsprogs', 'parted', 'cloud-guest-utils']
    with chroot_mounts(root), inhibit_services(root):
        run('chroot', root, 'apt-get', 'update')
        run('chroot', root, 'env', 'DEBIAN_FRONTEND=noninteractive',
            'apt-get', 'install', '-y', '--no-install-recommends', *packages)
        run('chroot', root, 'apt-get', 'purge', '-y', 'ifupdown')
        # chroot changes the filesystem, not the inherited host environment.
        run('chroot', root, 'env', '-i',
            'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'HOME=/root',
            'USER=root', 'LOGNAME=root', 'LANG=C', 'LC_ALL=C',
            'DEBIAN_FRONTEND=noninteractive',
            'bash', '/usr/share/libitina/YuiHirasawa/install-debian.sh')
        verify_guest_commands(root, bootable)
        run('chroot', root, 'apt-get', 'clean')
        install_desktop_appearance(root)
        run('chroot', root, '/usr/bin/python3',
            '/usr/share/libitina/Applications/install.py', '--system')
        configure_oobe(root)
        run('chroot', root, 'systemctl', 'enable', 'ssh', 'NetworkManager', 'lightdm')
        run('chroot', root, 'systemctl', 'set-default', 'graphical.target')
        # Passwordless root is not enabled. Set a password via --root-password.
        if args.root_password:
            run('chroot', root, 'chpasswd', input=f'root:{args.root_password}\n')
        # Rebrand only after installers that inspect the base distribution finish.
        run('chroot', root, '/usr/bin/python3', '/usr/share/libitina/DeNura/main.py')
    # Avoid shipping identical machine IDs and SSH host keys across images.
    write(root, '/etc/machine-id', '')
    for key in (root / 'etc/ssh').glob('ssh_host_*'):
        key.unlink()
    write(root, '/etc/systemd/system/regenerate-ssh-keys.service',
          '[Unit]\nDescription=Generate unique SSH host keys\nBefore=ssh.service\n'
          'ConditionPathExistsGlob=!/etc/ssh/ssh_host_*_key\n'
          '[Service]\nType=oneshot\nExecStart=/usr/bin/ssh-keygen -A\n'
          '[Install]\nWantedBy=multi-user.target\n')
    (root / 'etc/systemd/system/multi-user.target.wants').mkdir(parents=True, exist_ok=True)
    link = root / 'etc/systemd/system/multi-user.target.wants/regenerate-ssh-keys.service'
    link.symlink_to('../regenerate-ssh-keys.service')


def verify_guest_commands(root, bootable):
    commands = ['python3', 'labwc', 'lightdm', 'lightdm-gtk-greeter', 'nmcli',
                'sshd', 'ssh-keygen', 'sudo', 'systemctl', 'loginctl', 'localectl',
                'timedatectl', 'hostnamectl', 'useradd', 'userdel', 'chpasswd',
                'locale-gen', 'debconf-set-selections', 'dpkg-reconfigure', 'dbus-run-session']
    if bootable:
        commands += ['grub-install', 'update-grub', 'growpart', 'resize2fs']
    run('chroot', root, 'sh', '-ec',
        'for command do command -v "$command" >/dev/null || '
        '{ echo "Missing guest command: $command" >&2; exit 1; }; done',
        'verify-guest-commands', *commands)


def install_desktop_appearance(root):
    bundled = root / 'usr/share/libitina'
    source = bundled / 'Applications/Internal/Global/DeletescapeUiThemeInstall/themes'
    for name in ('DeletescapeUi', 'DeletescapeUi-Dark'):
        shutil.copytree(source / name, root / 'usr/share/themes' / name, dirs_exist_ok=True)
    fonts = '/usr/share/fonts/truetype/inclusive-sans'
    shutil.copytree(bundled / 'Fonts/InclusiveSans', root / fonts.lstrip('/'), dirs_exist_ok=True)
    run('chroot', root, 'fc-cache', fonts)

    interface_font = 'Inclusive Sans 11'
    settings = ('[Settings]\ngtk-theme-name=DeletescapeUi\n'
                'gtk-application-prefer-dark-theme=false\n')
    # Keep the GTK font fallback system-wide so provisioning can rewrite user theme settings.
    write(root, '/etc/gtk-3.0/settings.ini', settings + f'gtk-font-name={interface_font}\n')
    write(root, '/etc/skel/.config/gtk-3.0/settings.ini', settings)
    write(root, '/usr/share/glib-2.0/schemas/99_libitina.gschema.override',
          "[org.gnome.desktop.interface]\ngtk-theme='DeletescapeUi'\ncolor-scheme='default'\n"
          f"font-name='{interface_font}'\n")
    run('chroot', root, 'glib-compile-schemas', '/usr/share/glib-2.0/schemas')


def configure_oobe(root):
    run('chroot', root, 'useradd', '--create-home', '--shell', '/bin/bash', 'defaultuser0')
    # A locked password is accepted by Debian's dedicated lightdm-autologin PAM stack.
    write(root, '/etc/lightdm/lightdm.conf.d/50-libitina.conf',
          '[Seat:*]\ngreeter-session=lightdm-gtk-greeter\nuser-session=yuihirasawa\n')
    write(root, '/etc/lightdm/lightdm.conf.d/90-libitina-oobe.conf',
          '[Seat:*]\nautologin-user=defaultuser0\nautologin-user-timeout=0\n'
          'autologin-session=libitina-oobe\n')
    write(root, '/usr/share/wayland-sessions/libitina-oobe.desktop',
          '[Desktop Entry]\nName=Libitina Setup\nType=Application\n'
          'Exec=/bin/sh /usr/share/libitina/Setup/oobe-session.sh\n'
          'DesktopNames=labwc;\nX-LightDM-Session-Type=wayland\n')
    write(root, '/etc/sudoers.d/libitina-oobe',
          'defaultuser0 ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block libitina-setup.service, '
          '/usr/bin/systemctl poweroff\n',
          0o440)
    write(root, '/etc/systemd/system/libitina-setup.service',
          '[Unit]\nDescription=Apply Libitina first-boot setup\n'
          'ConditionPathExists=!/var/lib/libitina/setup-complete\n'
          '[Service]\nType=oneshot\n'
          'ExecStart=/usr/bin/python3 /usr/share/libitina/Setup/provision.py\n'
          'ExecStopPost=/usr/bin/systemctl --no-block start lightdm.service\n'
          'TimeoutStartSec=180\n')
    write(root, '/etc/systemd/system/libitina-oobe-recovery.service',
          '[Unit]\nDescription=Recover interrupted Libitina setup\n'
          'Before=lightdm.service\n'
          'ConditionPathExists=/var/lib/libitina-setup-in-progress\n'
          'ConditionPathExists=!/var/lib/libitina/setup-complete\n'
          '[Service]\nType=oneshot\n'
          'ExecStart=/usr/bin/python3 /usr/share/libitina/Setup/provision.py --recover-oobe\n'
          '[Install]\nWantedBy=graphical.target\n')
    run('chroot', root, 'systemctl', 'enable', 'libitina-oobe-recovery.service')


def install_expand_service(root):
    write(root, '/usr/local/sbin/expand-home-partition', '''#!/bin/sh
set -eu
# Locate the partition by its filesystem label, not a hard-coded /dev name.
part=$(readlink -f /dev/disk/by-label/DEBHOME)
case "$part" in
  /dev/nvme*n*p[0-9]*|/dev/mmcblk*p[0-9]*) disk=${part%p[0-9]*}; number=${part##*p} ;;
  /dev/*[0-9]) disk=${part%[0-9]*}; number=${part##*[!0-9]} ;;
  *) echo "Cannot parse partition path: $part" >&2; exit 1 ;;
esac
[ "$number" = 3 ] || { echo "Expected partition 3" >&2; exit 1; }
# growpart returns nonzero when no growth is needed on some versions.
growpart "$disk" "$number" || true
resize2fs "$part"
touch /var/lib/home-partition-expanded
''', 0o755)
    write(root, '/etc/systemd/system/expand-home.service',
          '[Unit]\nDescription=Expand /home partition to fill disk\n'
          'After=local-fs.target\nConditionPathExists=!/var/lib/home-partition-expanded\n'
          '[Service]\nType=oneshot\nExecStart=/usr/local/sbin/expand-home-partition\n'
          '[Install]\nWantedBy=multi-user.target\n')
    link = root / 'etc/systemd/system/multi-user.target.wants/expand-home.service'
    link.symlink_to('../expand-home.service')


def make_zip(root, output):
    # ZIP does not faithfully represent all Unix metadata (e.g. device nodes,
    # ownership, capabilities). This is a convenience archive, not a perfect backup.
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as z:
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                p = Path(base) / name
                rel = p.relative_to(root).as_posix()
                if p.is_symlink():
                    info = zipfile.ZipInfo(rel)
                    info.create_system = 3
                    info.external_attr = (p.lstat().st_mode & 0xFFFF) << 16
                    z.writestr(info, os.readlink(p))
                elif p.is_file():
                    z.write(p, rel)
                elif p.is_dir():
                    z.write(p, rel + '/')


def create_raw(path, size_gib, root_gib):
    # 512 MiB ESP, fixed root, remaining capacity for home.
    with open(path, 'wb') as f:
        f.truncate(size_gib * GIB)
    run('parted', '-s', path, 'mklabel', 'gpt')
    run('parted', '-s', path, 'mkpart', 'ESP', 'fat32', '1MiB', '513MiB')
    run('parted', '-s', path, 'set', '1', 'esp', 'on')
    run('parted', '-s', path, 'mkpart', 'root', 'ext4', '513MiB', f'{513 + root_gib * 1024}MiB')
    run('parted', '-s', path, 'mkpart', 'home', 'ext4', f'{513 + root_gib * 1024}MiB', '100%')


def install_bootloader(root, arch):
    target = TARGETS[arch]
    with chroot_mounts(root):
        run('chroot', root, 'grub-install', f'--target={target["grub"]}',
            '--efi-directory=/boot/efi', '--boot-directory=/boot',
            '--removable', '--no-nvram')
        run('chroot', root, 'update-grub')
    fallback = root / 'boot/efi/EFI/BOOT' / target['efi']
    if not fallback.is_file():
        raise RuntimeError(f'GRUB did not produce the UEFI fallback loader: {fallback}')


def build_disk(root, args, raw, expand):
    create_raw(raw, args.size, args.root_size)
    loop = run('losetup', '--find', '--show', '--partscan', raw, capture=True).stdout.strip()
    try:
        run('udevadm', 'settle')
        p1, p2, p3 = (Path(f'{loop}p{i}') for i in (1, 2, 3))
        for p in (p1, p2, p3):
            if not p.exists():
                raise RuntimeError(f'Loop partition not found: {p}')
        run('mkfs.vfat', '-F', '32', '-n', 'DEBEFI', p1)
        run('mkfs.ext4', '-F', '-L', 'DEBROOT', p2)
        run('mkfs.ext4', '-F', '-L', 'DEBHOME', p3)
        with mounted(p2, root):
            with mounted(p1, root / 'boot/efi'):
                with mounted(p3, root / 'home'):
                    bootstrap(root, args)
                    install_guest(root, args, True)
                    if expand:
                        install_expand_service(root)
                    install_bootloader(root, args.arch)
    finally:
        run('losetup', '-d', loop)


def finalize_output(output):
    # sudo is needed for the build, but the host artifact belongs to its caller.
    uid = int(os.environ.get('SUDO_UID', os.getuid()))
    gid = int(os.environ.get('SUDO_GID', os.getgid()))
    os.chown(output, uid, gid)
    output.chmod(0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('format', choices=['zip', 'img', 'qcow2'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--arch', choices=TARGETS, default='amd64',
                        help='guest architecture (default amd64); supports cross builds')
    parser.add_argument('--release', default='trixie')
    parser.add_argument('--mirror', default='http://deb.debian.org/debian')
    parser.add_argument('--hostname', default='FuckDavidBaszucki')
    parser.add_argument('--size', type=int, default=16,
                        help='raw image size in GiB (default 16); qcow2 always 20')
    parser.add_argument('--root-size', type=int, default=10,
                        help='system partition size in GiB (default 10)')
    parser.add_argument('--root-password', default=None,
                        help='INSECURE: visible in process arguments; prefer offline setup')
    parser.add_argument('--work-dir', type=Path,
                        help='temporary build storage (default /var/tmp; ignores TMPDIR)')
    parser.add_argument('--force-tmp', action='store_true',
                        help='allow /tmp and RAM filesystems; default to TMPDIR when --work-dir is omitted')
    parser.add_argument('--_mount-namespace', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('must run as root on Linux')
    if sys.platform != 'linux':
        parser.error('Linux build host required')
    if not args._mount_namespace:
        require('unshare')
        os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private',
                            sys.executable, str(Path(__file__).resolve()),
                            *sys.argv[1:], '--_mount-namespace'])
    if args.format == 'qcow2':
        args.size = 20
    if args.size * 1024 <= 513 + args.root_size * 1024 + 512:
        parser.error('image too small: need ESP + root + at least 512 MiB for /home')
    commands = ['debootstrap', 'chroot', 'mount', 'umount', 'git', 'findmnt']
    if args.format != 'zip':
        commands += ['parted', 'losetup', 'udevadm', 'mkfs.vfat', 'mkfs.ext4']
    if args.format == 'qcow2':
        commands.append('qemu-img')
    for command in commands:
        require(command)
    require_cross_emulation(args.arch)
    args.output = args.output.absolute()
    if args.output.exists():
        parser.error('output already exists; refusing to overwrite')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = (args.work_dir if args.work_dir is not None else
                Path(tempfile.gettempdir()) if args.force_tmp else Path('/var/tmp'))
    work_dir.mkdir(parents=True, exist_ok=True)
    check_build_storage(work_dir, args.force_tmp)
    check_build_storage(args.output.parent, args.force_tmp)
    print(f'Build storage: {work_dir.resolve()}', flush=True)
    with build_directory(work_dir) as temp:
        root = Path(temp) / 'root'
        root.mkdir()
        if args.format == 'zip':
            bootstrap(root, args)
            install_guest(root, args, False)
            make_zip(root, args.output)
        else:
            raw = args.output if args.format == 'img' else Path(temp) / 'disk.img'
            build_disk(root, args, raw, args.format == 'img')
            if args.format == 'qcow2':
                run('qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2',
                    '-o', 'compat=1.1', raw, args.output)
    finalize_output(args.output)
    print(f'Created: {args.output}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
