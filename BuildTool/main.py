#!/usr/bin/env python3
"""Build Debian rootfs ZIPs, expandable raw images, or sparse 20-GiB QCOW2 disks.

Linux x86_64 host, root privileges, Debian amd64 guest, UEFI boot.
Requires: debootstrap, parted, util-linux, e2fsprogs, dosfstools, grub-efi-amd64-bin,
          grub-common, qemu-utils (for qcow2), git, python3.

WARNING: Builds use loop devices and mount namespaces; run on a trusted Linux host.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile

GIB = 1024 ** 3
MIB = 1024 ** 2


def run(*args, capture=False, **kw):
    print('+', ' '.join(map(str, args)), flush=True)
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


def bootstrap(root, args):
    # Resolve the source repository independently of the caller's working directory.
    source = Path(__file__).resolve().parent
    metadata = {
        'builder_username': os.environ.get('SUDO_USER') or getpass.getuser(),
        'builder_hostname': socket.gethostname(),
        'build_datetime': datetime.now(timezone.utc).isoformat(),
        'git_commit': run('git', '-C', source, 'rev-parse', 'HEAD',
                          capture=True).stdout.strip(),
        'git_branch': run('git', '-C', source, 'rev-parse', '--abbrev-ref', 'HEAD',
                          capture=True).stdout.strip(),
    }
    run('debootstrap', '--arch=amd64', '--variant=minbase', args.release,
        root, args.mirror)
    write(root, '/etc/libitina-info', json.dumps(metadata, indent=2) + '\n', 0o644)
    write(root, '/etc/hostname', args.hostname + '\n')
    write(root, '/etc/hosts', f'127.0.0.1 localhost\n127.0.1.1 {args.hostname}\n')
    write(root, '/etc/apt/sources.list',
          f'deb {args.mirror} {args.release} main\n'
          f'deb {args.mirror} {args.release}-updates main\n'
          f'deb http://security.debian.org/debian-security {args.release}-security main\n')
    write(root, '/etc/network/interfaces',
          'auto lo\niface lo inet loopback\n\nauto enp1s0\nallow-hotplug enp1s0\niface enp1s0 inet dhcp\n')
    write(root, '/etc/resolv.conf', 'nameserver 1.1.1.1\n')
    write(root, '/etc/fstab',
          'LABEL=DEBROOT / ext4 defaults 0 1\n'
          'LABEL=DEBHOME /home ext4 defaults,nofail 0 2\n'
          'LABEL=DEBEFI /boot/efi vfat umask=0077 0 1\n')
    (root / 'home').mkdir(exist_ok=True)
    (root / 'boot/efi').mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def mounted(source, destination, *options):
    destination.mkdir(parents=True, exist_ok=True)
    run('mount', *options, source, destination)
    try:
        yield
    finally:
        run('umount', '-R', destination)


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
        yield
    finally:
        for dest in reversed(mounts):
            run('umount', '-R', dest)


def install_guest(root, args, bootable):
    packages = ['systemd-sysv', 'dbus', 'ca-certificates', 'iproute2',
                'ifupdown', 'isc-dhcp-client', 'sudo', 'openssh-server']
    if bootable:
        packages += ['linux-image-amd64', 'grub-efi-amd64-bin',
                     'grub-common', 'e2fsprogs', 'parted', 'cloud-guest-utils']
    with chroot_mounts(root):
        run('chroot', root, 'apt-get', 'update')
        run('chroot', root, 'env', 'DEBIAN_FRONTEND=noninteractive',
            'apt-get', 'install', '-y', '--no-install-recommends', *packages)
        run('chroot', root, 'systemctl', 'enable', 'ssh')
        # Passwordless root is not enabled. Set a password via --root-password.
        if args.root_password:
            run('chroot', root, 'chpasswd', input=f'root:{args.root_password}\n')
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
                    # Removable-media fallback bootloader: EFI/BOOT/BOOTX64.EFI.
                    with chroot_mounts(root):
                        run('chroot', root, 'grub-install', '--target=x86_64-efi',
                            '--efi-directory=/boot/efi', '--boot-directory=/boot',
                            '--removable', '--no-nvram')
                        run('chroot', root, 'update-grub')
    finally:
        run('losetup', '-d', loop)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('format', choices=['zip', 'img', 'qcow2'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--release', default='trixie')
    parser.add_argument('--mirror', default='http://deb.debian.org/debian')
    parser.add_argument('--hostname', default='debian')
    parser.add_argument('--size', type=int, default=8,
                        help='raw image size in GiB (default 8); qcow2 always 20')
    parser.add_argument('--root-size', type=int, default=4,
                        help='system partition size in GiB (default 4)')
    parser.add_argument('--root-password', default=None,
                        help='INSECURE: visible in process arguments; prefer offline setup')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('must run as root on Linux')
    if sys.platform != 'linux':
        parser.error('Linux build host required')
    if args.format == 'qcow2':
        args.size = 20
    if args.size * 1024 <= 513 + args.root_size * 1024 + 512:
        parser.error('image too small: need ESP + root + at least 512 MiB for /home')
    commands = ['debootstrap', 'chroot', 'mount', 'umount', 'git']
    if args.format != 'zip':
        commands += ['parted', 'losetup', 'udevadm', 'mkfs.vfat', 'mkfs.ext4']
    if args.format == 'qcow2':
        commands.append('qemu-img')
    for command in commands:
        require(command)
    args.output = args.output.absolute()
    if args.output.exists():
        parser.error('output already exists; refusing to overwrite')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='debian-builder-') as temp:
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
    print(f'Created: {args.output}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
