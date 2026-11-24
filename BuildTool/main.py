#!/usr/bin/env python3
"""Build Debian rootfs ZIPs, expandable raw images, or sparse 20-GiB QCOW2 disks.

Linux amd64/arm64 host, root privileges, Debian amd64/arm64 guest, UEFI boot.
Cross builds require QEMU user emulation registered with binfmt_misc (F flag).
Host requires: debootstrap, coreutils, mount, util-linux, parted, udev,
               e2fsprogs, dosfstools, qemu-utils (for qcow2), git, python3,
               squashfs-tools (for --recovery), sfdisk (for --rootless).
Guest packages (including GRUB) are installed automatically.

WARNING: Builds use loop devices and mount namespaces; run on a trusted Linux host.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import getpass
import hashlib
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
RECOVERY_MIB = 2048
ROOTLESS_HOME_TYPE = '5efce933-a09b-456d-9a5c-3885f4120fde'
STEAM_CLIENT_BETA = 'steamdeck_publicbeta'
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


def bootstrap_base(root, args):
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
    write(root, '/etc/hostname', args.hostname + '\n')
    write(root, '/etc/hosts', f'127.0.0.1 localhost\n127.0.1.1 {args.hostname}\n')
    configure_apt_sources(root, args)
    write(root, '/etc/resolv.conf', 'nameserver 1.1.1.1\n')


def configure_apt_sources(root, args, *, holo=False):
    components = 'main contrib non-free-firmware' if holo else 'main'
    sources = (f'deb {args.mirror} {args.release} {components}\n'
               f'deb {args.mirror} {args.release}-updates {components}\n'
               f'deb http://security.debian.org/debian-security {args.release}-security {components}\n')
    if holo and args.release == 'trixie':
        sources += f'deb {args.mirror} trixie-backports main contrib\n'
    write(root, '/etc/apt/sources.list', sources)


def configure_system(root, args):
    variant = 'holo' if getattr(args, 'holo', False) else 'normal'
    install_build_metadata(root, args, variant)
    install_libitina(root, args.output)
    write(root, '/etc/fstab',
          'LABEL=DEBROOT / ext4 defaults 0 1\n'
          'LABEL=DEBHOME /home ext4 defaults,nofail 0 2\n'
          'LABEL=DEBEFI /boot/efi vfat umask=0077 0 1\n')
    (root / 'home').mkdir(exist_ok=True)
    (root / 'boot/efi').mkdir(parents=True, exist_ok=True)


def bootstrap(root, args):
    bootstrap_base(root, args)
    configure_system(root, args)


def install_build_metadata(root, args, variant):
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
        'is_debug': getattr(args, 'debug', False),
        'is_dirty': bool(run('git', '-C', source, 'status', '--porcelain',
                             '--untracked-files=normal', capture=True).stdout.strip()),
        'variant': variant,
    }
    write(root, '/etc/libitina-info', json.dumps(metadata, indent=2) + '\n', 0o644)


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
    holo = getattr(args, 'holo', False)
    debug = getattr(args, 'debug', False)
    packages = ['systemd-sysv', 'libpam-systemd', 'tzdata', 'dbus', 'ca-certificates', 'iproute2',
                'network-manager', 'sudo', 'openssh-server', 'lightdm',
                'lightdm-gtk-greeter', 'xserver-xorg', 'locales', 'keyboard-configuration', 'zstd',
                'gsettings-desktop-schemas', 'libglib2.0-bin', 'dconf-gsettings-backend',
                'desktop-file-utils', 'fontconfig']
    if bootable:
        packages += [f'linux-image-{args.arch}', f'grub-efi-{args.arch}-bin',
                     'grub-common', 'grub2-common', 'e2fsprogs', 'parted', 'cloud-guest-utils']
    with chroot_mounts(root), inhibit_services(root):
        if holo:
            configure_apt_sources(root, args, holo=True)
            if args.arch == 'amd64':
                run('chroot', root, 'dpkg', '--add-architecture', 'i386')
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
        install_desktop_appearance(root)
        run('chroot', root, '/usr/bin/python3',
            '/usr/share/libitina/Applications/install.py', '--system')
        if holo:
            configure_holo(root, args.arch, autologin=args.holo_autologin, debug=debug)
        else:
            configure_oobe(root)
        run('chroot', root, 'apt-get', 'clean')
        run('chroot', root, 'systemctl', 'enable', 'ssh', 'NetworkManager', 'lightdm')
        run('chroot', root, 'systemctl', 'set-default', 'graphical.target')
        configure_guest_authentication(root, args)
        # Rebrand only after installers that inspect the base distribution finish.
        run('chroot', root, '/usr/bin/python3', '/usr/share/libitina/DeNura/main.py')
    # Avoid shipping identical machine IDs and SSH host keys across images.
    write(root, '/etc/machine-id', '')
    regenerate_ssh_host_keys(root)


def configure_guest_authentication(root, args):
    password = args.root_password
    if getattr(args, 'debug', False):
        if password is None:
            password = 'debug'
        write(root, '/etc/ssh/sshd_config.d/00-libitina-debug.conf',
              'PasswordAuthentication yes\nPermitRootLogin yes\n')
    if password:
        run('chroot', root, 'chpasswd', input=f'root:{password}\n')


def regenerate_ssh_host_keys(root):
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


def seed_holo_steam(root, arch):
    steam = '/home/steamos/.local/share/Steam'
    links = '/home/steamos/.steam'
    as_steamos = ('chroot', root, 'runuser', '-u', 'steamos', '--')
    curl = ('curl', '--fail', '--silent', '--show-error', '--location', '--retry', '3')
    run(*as_steamos, 'mkdir', '-p', steam, links)
    with tempfile.TemporaryDirectory(prefix='steam-', dir=root / 'tmp') as directory:
        staging = f'/tmp/{Path(directory).name}'
        run('chroot', root, 'chown', 'steamos:steamos', staging)
        if arch == 'amd64':
            run(*as_steamos, *curl, '-o', f'{staging}/steam.deb',
                'https://repo.steampowered.com/steam/archive/stable/steam_latest.deb')
            run(*as_steamos, 'dpkg-deb', '--extract', f'{staging}/steam.deb', staging)
            bootstrap = f'{staging}/usr/lib/steam/bootstraplinux_ubuntu12_32.tar.xz'
            run(*as_steamos, 'cp', bootstrap, f'{steam}/bootstrap.tar.xz')
            run(*as_steamos, 'tar', '-xf', f'{steam}/bootstrap.tar.xz', '-C', steam)
            run(*as_steamos, 'test', '-x', f'{steam}/steam.sh')
        else:
            cdn = 'https://client-update.steamstatic.com'
            manifest = run(*as_steamos, *curl, f'{cdn}/steam_client_{STEAM_CLIENT_BETA}_linuxarm64',
                           capture=True).stdout
            # Install shared assets and all ARM64 packages from the current client build.
            blocks = dict(re.findall(r'"([a-z0-9_]+)"\s*\{([^{}]*)\}', manifest))
            if 'bins_linuxarm64_linuxarm64' not in blocks:
                raise RuntimeError(f'Steam {STEAM_CLIENT_BETA} manifest has no native arm64 client')
            for package, block in blocks.items():
                if not package.endswith(('_all', '_linuxarm64')):
                    continue
                filename = re.search(r'"file"\s+"([a-z0-9_]+\.zip\.[0-9a-f]{40}(?:_[a-z0-9_]+)?)"', block)
                checksum = re.search(r'"sha2"\s+"([0-9a-f]{64})"', block)
                if filename is None or checksum is None:
                    raise RuntimeError(f'Invalid Steam arm64 manifest entry: {package}')
                run(*as_steamos, *curl, '-o', f'{staging}/{filename[1]}', f'{cdn}/{filename[1]}')
                with (Path(directory) / filename[1]).open('rb') as download:
                    if hashlib.file_digest(download, 'sha256').hexdigest() != checksum[1]:
                        raise RuntimeError(f'Steam arm64 archive checksum mismatch: {filename[1]}')
                # Valve's ZIP entries can use backslashes as path separators.
                run(*as_steamos, 'bsdtar', '-xf', f'{staging}/{filename[1]}',
                    '-C', steam, '-s', r',\\,/,g')
            run(*as_steamos, 'sh', '-ec', r'''
cd "$1"
touch .steam-enable-steamrt64-client
# Leave steamrt64 for the updater's separate x86-64 runtime.
chmod -R u+X steamrtarm64
chmod u+x steamrtarm64/steam steamrtarm64/steamwebhelper steamrtarm64/steamwebhelper.sh \
    steamrtarm64/gldriverquery steamrtarm64/vulkandriverquery steamrtarm64/steamsysinfo
ln -sfnT ../.local/share/Steam/linuxarm64 "$2/sdkarm64"
''', 'seed-steam-arm64', steam, links)
    run(*as_steamos, 'mkdir', '-p', f'{steam}/package')
    run(*as_steamos, 'tee', f'{steam}/package/beta', input=f'{STEAM_CLIENT_BETA}\n', capture=True)
    for name in ('root', 'steam'):
        run(*as_steamos, 'ln', '-sfnT', '../.local/share/Steam', f'{links}/{name}')


def configure_holo(root, arch, *, autologin=False, debug=False):
    packages = ['alsa-ucm-conf', 'curl', 'fonts-dejavu-core', 'libgl1-mesa-dri', 'mesa-vulkan-drivers',
                'lsof', 'pipewire-pulse', 'steam-devices', 'xwayland']
    if arch == 'amd64':
        packages += ['steam-installer', 'steam-libs-i386:i386', 'xauth', 'xvfb',
                     'libgl1-mesa-dri:i386', 'mesa-vulkan-drivers:i386',
                     'firmware-amd-graphics', 'firmware-atheros', 'firmware-mediatek',
                     'firmware-realtek']
        launch = 'exec /usr/games/steam "$@"\n'
    else:
        packages += ['libarchive-tools', 'libatomic1', 'libgtk2.0-0t64', 'libibus-1.0-5',
                     'libnss3', 'libopenal1', 'libva-x11-2', 'libvpx-dev', 'libxss1', 'libxtst6']
        launch = '''client="$HOME/.local/share/Steam/steamrtarm64"
export LD_LIBRARY_PATH="$client:/usr/local/lib/libitina-steam${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# The native Steam binary returns 42 when it needs its launcher to restart it.
for attempt in 1 2 3; do
    "$client/steam" "$@"
    status=$?
    if [ "$status" -ne 42 ]; then
        exit "$status"
    fi
    if [ "$attempt" -lt 3 ]; then
        echo "Steam requested a restart after its update. Relaunching." >&2
    fi
done
echo "Steam kept requesting restarts after three attempts." >&2
exit 42
'''
    gamescope = run('chroot', root, 'apt-cache', 'policy', 'gamescope', capture=True).stdout
    gamescope_available = re.search(r'^\s*Candidate: \d', gamescope, re.MULTILINE) is not None
    if gamescope_available:
        packages.append('gamescope')
    else:
        print('Gamescope package unavailable for this guest, using Labwc for Holo.', flush=True)
    run('chroot', root, 'apt-get', 'install', '-y', '--no-install-recommends', *packages)
    if arch == 'arm64':
        # Keep the beta's legacy libvpx lookup local to the Steam process.
        compat = root / 'usr/local/lib/libitina-steam'
        compat.mkdir(parents=True, exist_ok=True)
        (compat / 'libvpx.so.6').symlink_to('/usr/lib/aarch64-linux-gnu/libvpx.so')
    run('chroot', root, 'useradd', '--create-home', '--user-group',
        '--shell', '/bin/bash', 'steamos')
    run('chroot', root, 'chpasswd', input='steamos:gaben\n')
    write(root, '/etc/sudoers.d/libitina-holo', 'steamos ALL=(ALL:ALL) ALL\n', 0o440)
    if not debug:
        write(root, '/etc/ssh/sshd_config.d/90-libitina-holo.conf',
              'Match User steamos\n    AuthenticationMethods publickey\nMatch all\n')
    seed_holo_steam(root, arch)

    write(root, '/usr/local/bin/libitina-steam', '#!/bin/sh\n' + launch, 0o755)
    if arch == 'amd64':
        update_holo_steam(root)
    install_holo_sessions(root, gamescope_available, autologin)


def update_holo_steam(root):
    # Use Steam's updater so the image also contains its installed package state.
    as_steamos = ('chroot', root, 'runuser', '-u', 'steamos', '--')
    run(*as_steamos, 'timeout', '--kill-after=30s', '20m', 'xvfb-run', '-a',
        '/usr/local/bin/libitina-steam', '-clientbeta', STEAM_CLIENT_BETA,
        '-forcesteamupdate', '-exitsteam')
    run(*as_steamos, 'sh', '-ec', '''
for manifest in "$HOME"/.local/share/Steam/package/steam_client_*.installed; do
    if [ -s "$manifest" ]; then
        exit 0
    fi
done
echo 'Steam updater did not record an installed client manifest' >&2
exit 1
''')


def install_holo_sessions(root, gamescope_available, autologin):
    launcher = '/usr/local/bin/libitina-holo-session'
    write(root, launcher, '''#!/bin/sh
set -eu
profile=$1
compositor=$2
shift 2
export PATH="$PATH:/usr/games"
export XDG_SESSION_TYPE=wayland
unset DISPLAY WAYLAND_DISPLAY

original_umask=$(umask)
umask 077
log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/libitina/holo"
mkdir -p "$log_dir"
log="$log_dir/$profile.log"
if [ -f "$log" ]; then
    mv -f "$log" "$log.previous"
fi
exec >"$log" 2>&1
printf '%s Starting %s with %s\\n' "$(date -Is)" "$profile" "$compositor"
umask "$original_umask"

if [ "$compositor" = terminal ]; then
    export XDG_CURRENT_DESKTOP=labwc
    exec labwc --session /usr/bin/foot
fi

set -- /usr/local/bin/libitina-steam -steamdeck -steamos3 -gamepadui -clientbeta @CLIENT_BETA@ "$@"
printf 'Steam command: %s\\n' "$*"
if [ "$compositor" = gamescope ]; then
    export XDG_CURRENT_DESKTOP=gamescope
    exec gamescope -e -- "$@"
fi
if [ "$compositor" = auto ] && command -v gamescope >/dev/null; then
    XDG_CURRENT_DESKTOP=gamescope gamescope -e -- "$@" && exit 0
    echo "Gamescope exited with an error. Starting the Steam session in Labwc." >&2
fi
export XDG_CURRENT_DESKTOP=labwc
# Steam uses the Wayland display marker to select the Game Mode power menu.
# Keep the compositor alive when Steam exits to apply an update.
exec labwc --startup "sh -c 'export GAMESCOPE_WAYLAND_DISPLAY=\\"\\$WAYLAND_DISPLAY\\"; exec \\"\\$@\\"' steam $*"
'''.replace('@CLIENT_BETA@', STEAM_CLIENT_BETA), 0o755)
    profiles = [
        ('default', 'Default', ()),
        ('force-gpu', 'CEF Force GPU', ('-cef-force-gpu',)),
        ('x11', 'CEF X11', ('-cef-ozone-platform=x11',)),
        ('no-sandbox', 'CEF Sandbox Off', ('-no-cef-sandbox',)),
        ('angle', 'CEF ANGLE Vulkan', ('-cef-use-gl=angle', '-cef-use-angle=vulkan')),
        ('combined', 'CEF GPU, X11, ANGLE Vulkan, Sandbox Off',
         ('-cef-force-gpu', '-cef-ozone-platform=x11', '-cef-use-gl=angle',
          '-cef-use-angle=vulkan', '-no-cef-sandbox')),
    ]
    sessions = [('libitina-holo', 'Libitina Holo', 'auto', ()),
                ('libitina-holo-terminal', 'Libitina Holo (Terminal)', 'terminal', ())]
    compositors = [('labwc', 'Labwc')]
    if gamescope_available:
        compositors.append(('gamescope', 'Gamescope'))
    for compositor, name in compositors:
        sessions.extend((f'libitina-holo-{compositor}-{profile}', f'Holo {name} ({label})',
                         compositor, flags) for profile, label, flags in profiles)
    for profile, name, compositor, flags in sessions:
        command = ' '.join((launcher, profile, compositor, *flags))
        write(root, f'/usr/share/wayland-sessions/{profile}.desktop',
              f'[Desktop Entry]\nName={name}\nType=Application\nExec={command}\n'
              'X-LightDM-Session-Type=wayland\n')

    automatic = ('autologin-user=steamos\nautologin-user-timeout=0\n'
                 'autologin-session=libitina-holo\n') if autologin else ''
    write(root, '/etc/lightdm/lightdm.conf.d/90-libitina-holo.conf',
          '[Seat:*]\ngreeter-session=lightdm-gtk-greeter\n'
          'greeter-hide-users=false\nuser-session=libitina-holo\n' + automatic)


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


def build_recovery(work, args, *, system_root=None):
    root = work / 'recovery-root'
    root.mkdir()
    artifacts = work / 'recovery'
    artifacts.mkdir()
    bootstrap_base(root, args)
    if system_root is not None:
        # Preserve ownership, links, device nodes and extended attributes.
        run('cp', '-a', f'{root}/.', system_root)
    install_build_metadata(root, args, 'recovery')
    debug = getattr(args, 'debug', False)
    write(root, '/etc/fstab', '')
    write(root, '/etc/initramfs-tools/modules', 'squashfs\noverlay\n')
    write(root, '/etc/initramfs-tools/scripts/local-bottom/recovery-overlay', '''#!/bin/sh
case "${1:-}" in
    prereqs) exit 0 ;;
esac
. /scripts/functions

# The initramfs moves /run into the new root before handing off to systemd.
ram=/run/recovery-overlay
setup_overlay() {
    mkdir -p "$ram" &&
    mount -t tmpfs -o mode=0700 tmpfs "$ram" &&
    mkdir "$ram/base" "$ram/upper" "$ram/work" &&
    mount -n -o move "$rootmnt" "$ram/base" &&
    mount -t overlay -o "lowerdir=$ram/base,upperdir=$ram/upper,workdir=$ram/work" \\
        overlay "$rootmnt"
}
if ! setup_overlay; then
    panic "Could not mount the RecoveryOS RAM overlay"
    exit 1
fi
''', 0o755)
    write(root, '/etc/systemd/system/recovery-desktop.service', '''[Unit]
Description=RecoveryOS desktop
After=systemd-user-sessions.service
Conflicts=getty@tty1.service

[Service]
User=root
WorkingDirectory=/root
RuntimeDirectory=recovery-desktop
RuntimeDirectoryMode=0700
Environment=HOME=/root XDG_RUNTIME_DIR=/run/recovery-desktop LIBSEAT_BACKEND=seatd
ExecStart=/usr/bin/seatd-launch -- /usr/bin/dbus-run-session -- /usr/bin/labwc -s /usr/bin/foot
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical.target
''')
    with chroot_mounts(root), inhibit_services(root):
        run('chroot', root, 'apt-get', 'update')
        run('chroot', root, 'apt-get', 'install', '-y', '--no-install-recommends',
            'systemd-sysv', 'udev', 'dbus', 'seatd', 'labwc', 'foot', 'fonts-dejavu',
            'libgl1-mesa-dri', 'initramfs-tools', 'python3', f'linux-image-{args.arch}',
            *(['openssh-server'] if debug else []))
        if debug:
            configure_guest_authentication(root, args)
            run('chroot', root, 'systemctl', 'enable', 'ssh')
        run('chroot', root, 'systemctl', 'mask', 'getty@tty1.service', 'seatd.service')
        run('chroot', root, 'systemctl', 'enable', 'recovery-desktop.service')
        run('chroot', root, 'systemctl', 'set-default', 'graphical.target')
        run('chroot', root, 'apt-get', 'clean')
        denura = Path(__file__).resolve().parents[1] / 'DeNura/main.py'
        run('chroot', root, '/usr/bin/python3', '-', input=denura.read_text(encoding='utf-8'))
    write(root, '/etc/issue', 'Libitina RecoveryOS\n')
    write(root, '/etc/machine-id', '')
    if debug:
        regenerate_ssh_host_keys(root)
    kernel, = (root / 'boot').glob('vmlinuz-*')
    initrd = kernel.with_name(kernel.name.replace('vmlinuz-', 'initrd.img-', 1))
    shutil.copyfile(kernel, artifacts / 'vmlinuz')
    shutil.copyfile(initrd, artifacts / 'initrd.img')
    run('mksquashfs', root, artifacts / 'recovery.squashfs', '-noappend', '-e', 'boot')
    return artifacts


def install_rootless(root, home_uuid, efi_uuid):
    write(root, '/etc/fstab',
          '/run/libitina-rootless/state/home /home none bind 0 0\n'
          f'PARTUUID={efi_uuid} /boot/efi vfat ro,umask=0077 0 0\n')
    write(root, '/usr/lib/libitina/repart.d/10-home.conf',
          f'[Partition]\nType={ROOTLESS_HOME_TYPE}\nLabel=DEBHOME\nUUID={home_uuid}\n'
          'SizeMinBytes=512M\nFormat=ext4\nFactoryReset=yes\n')
    write(root, '/etc/initramfs-tools/conf.d/libitina-rootless', 'BOOT=libitina-rootless\n')
    write(root, '/etc/initramfs-tools/hooks/libitina-rootless', '''#!/bin/sh
set -e
[ "${1:-}" != prereqs ] || exit 0
. /usr/share/initramfs-tools/hook-functions

force_load overlay
copy_exec /usr/bin/cp /usr/lib/libitina/initramfs/cp
copy_exec /sbin/fsck
copy_exec /sbin/fsck.ext4
copy_exec /sbin/logsave
''', 0o755)
    write(root, '/etc/initramfs-tools/scripts/libitina-rootless', '''#!/bin/sh
# initramfs-tools sources this after its standard local boot functions.
mountroot() {
    # Prevent both fsck writes and ext4 journal replay on the system partition.
    readonly=y fastboot=y ROOTFLAGS='-o noload' local_mount_root
    local_device_setup 'PARTUUID=@HOME_UUID@' 'Libitina home partition'
    checkfs "$DEV" home ext4
    if ! mount_root_overlay "$DEV"; then
        # Leaving the rescue shell must never boot into a partially mounted root.
        while :; do
            panic 'Could not mount the Libitina root overlay. Repair the home volume and reboot.'
        done
    fi
}

mount_root_overlay() {
    # The initramfs moves /run and its mounts into the new root at handoff.
    local runtime=/run/libitina-rootless
    local state=$runtime/state
    local base=$runtime/base
    mkdir -m 0700 "$runtime" && mkdir "$state" "$base" &&
        mount -t ext4 -o rw "$1" "$state" &&
        mkdir -p "$state/upper" "$state/work" || return 1

    # Seed the setup account once per fresh home filesystem. Rename the finished
    # directory into place so an interrupted copy can resume on the next boot.
    if [ ! -d "$state/home" ]; then
        mkdir -p "$state/home.new" &&
            /usr/lib/libitina/initramfs/cp -a "$rootmnt/home/." "$state/home.new/" &&
            sync &&
            mv "$state/home.new" "$state/home" || return 1
    fi

    mount -n -o move "$rootmnt" "$base" &&
        mount -t overlay -o "lowerdir=$base,upperdir=$state/upper,workdir=$state/work" \\
            overlay "$rootmnt" &&
        mount -n -o bind "$state/home" "$rootmnt/home"
}
'''.replace('@HOME_UUID@', home_uuid), 0o755)
    with chroot_mounts(root):
        run('chroot', root, 'update-initramfs', '-u', '-k', 'all')


def install_expand_service(root, partition=3, home_device='/dev/disk/by-label/DEBHOME'):
    write(root, '/usr/local/sbin/expand-home-partition', '''#!/bin/sh
set -eu
# Resolve the partition on the current boot device.
part=$(readlink -f '@HOME_DEVICE@')
case "$part" in
  /dev/nvme*n*p[0-9]*|/dev/mmcblk*p[0-9]*) disk=${part%p[0-9]*}; number=${part##*p} ;;
  /dev/*[0-9]) disk=${part%[0-9]*}; number=${part##*[!0-9]} ;;
  *) echo "Cannot parse partition path: $part" >&2; exit 1 ;;
esac
[ "$number" = @PARTITION@ ] || { echo "Expected partition @PARTITION@" >&2; exit 1; }
# growpart returns nonzero when no growth is needed on some versions.
growpart "$disk" "$number" || true
resize2fs "$part"
touch /var/lib/home-partition-expanded
'''.replace('@PARTITION@', str(partition)).replace('@HOME_DEVICE@', home_device), 0o755)
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


def create_raw(path, size_gib, root_gib, recovery=False):
    # Keep /home last so it can grow when a raw image is flashed to a larger disk.
    with open(path, 'wb') as f:
        f.truncate(size_gib * GIB)
    run('parted', '-s', path, 'mklabel', 'gpt')
    run('parted', '-s', path, 'mkpart', 'ESP', 'fat32', '1MiB', '513MiB')
    run('parted', '-s', path, 'set', '1', 'esp', 'on')
    root_end = 513 + root_gib * 1024
    run('parted', '-s', path, 'mkpart', 'root', 'ext4', '513MiB', f'{root_end}MiB')
    home_start = root_end
    if recovery:
        home_start += RECOVERY_MIB
        run('parted', '-s', path, 'mkpart', 'recovery', f'{root_end}MiB', f'{home_start}MiB')
    run('parted', '-s', path, 'mkpart', 'home', 'ext4', f'{home_start}MiB', '100%')


def install_recovery(root, artifacts, partition, system_partition):
    image = artifacts / 'recovery.squashfs'
    if image.stat().st_size > RECOVERY_MIB * MIB:
        raise RuntimeError('RecoveryOS does not fit in the 2 GiB recovery partition')
    run('dd', f'if={image}', f'of={partition}', 'bs=4M', 'conv=fsync')
    recovery_uuid = run('blkid', '-s', 'PARTUUID', '-o', 'value', partition,
                        capture=True).stdout.strip()
    system_uuid = run('blkid', '-s', 'UUID', '-o', 'value', system_partition,
                      capture=True).stdout.strip()
    boot = root / 'boot/recovery'
    boot.mkdir()
    for name in ('vmlinuz', 'initrd.img'):
        shutil.copyfile(artifacts / name, boot / name)
    write(root, '/etc/default/grub.d/90-recovery.cfg',
          'GRUB_TIMEOUT_STYLE=menu\nGRUB_TIMEOUT=5\n')
    write(root, '/etc/grub.d/41_recovery', f"""#!/bin/sh
cat <<'EOF'
menuentry 'Libitina RecoveryOS' --id libitina-recovery {{
    search --no-floppy --fs-uuid --set=root {system_uuid}
    linux /boot/recovery/vmlinuz root=PARTUUID={recovery_uuid} rootfstype=squashfs ro
    initrd /boot/recovery/initrd.img
}}
EOF
""", 0o755)


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
    home_number = 4 if args.recovery else 3
    create_raw(raw, args.size, args.root_size, args.recovery)
    if args.rootless:
        run('sfdisk', '--part-type', raw, home_number, ROOTLESS_HOME_TYPE)
    loop = run('losetup', '--find', '--show', '--partscan', raw, capture=True).stdout.strip()
    try:
        run('udevadm', 'settle')
        partitions = [Path(f'{loop}p{i}') for i in range(1, home_number + 1)]
        for p in partitions:
            if not p.exists():
                raise RuntimeError(f'Loop partition not found: {p}')
        p1, p2 = partitions[:2]
        home = partitions[-1]
        run('mkfs.vfat', '-F', '32', '-n', 'DEBEFI', p1)
        run('mkfs.ext4', '-F', '-L', 'DEBROOT', p2)
        run('mkfs.ext4', '-F', '-L', 'DEBHOME', home)
        with mounted(p2, root):
            recovery = (build_recovery(root.parent, args, system_root=root)
                        if args.recovery else None)
            with mounted(p1, root / 'boot/efi'):
                # Preserve the factory home on the lower layer for first boot and reset.
                with (contextlib.nullcontext() if args.rootless else mounted(home, root / 'home')):
                    if recovery is None:
                        bootstrap(root, args)
                    else:
                        configure_system(root, args)
                    install_guest(root, args, True)
                    home_device = '/dev/disk/by-label/DEBHOME'
                    if args.rootless:
                        home_uuid, efi_uuid = (
                            run('blkid', '-s', 'PARTUUID', '-o', 'value', p,
                                capture=True).stdout.strip() for p in (home, p1))
                        install_rootless(root, home_uuid, efi_uuid)
                        home_device = f'/dev/disk/by-partuuid/{home_uuid}'
                    if expand:
                        install_expand_service(root, home_number, home_device)
                    if recovery:
                        install_recovery(root, recovery, partitions[2], p2)
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
    parser.add_argument('--size', type=int,
                        help='disk image size in GiB (default 16 for img, 20 for qcow2)')
    parser.add_argument('--root-size', type=int, default=10,
                        help='system partition size in GiB (default 10)')
    parser.add_argument('--recovery', action='store_true',
                        help='build a RecoveryOS ZIP or add a bootable recovery partition to a disk')
    parser.add_argument('--rootless', action='store_true',
                        help='use a read-only system partition with a persistent /home-backed overlay (img and qcow2)')
    parser.add_argument('--holo', action='store_true',
                        help='preinstall Steam and add Holo gaming sessions for steamos')
    parser.add_argument('--holo-autologin', action='store_true',
                        help='skip the Holo session picker and log in as steamos (requires --holo)')
    parser.add_argument('--debug', action='store_true',
                        help='enable SSH password login and default the root password to "debug"')
    parser.add_argument('--root-password', default=None,
                        help='INSECURE: visible in process arguments; prefer offline setup')
    parser.add_argument('--work-dir', type=Path,
                        help='temporary build storage (default /var/tmp; ignores TMPDIR)')
    parser.add_argument('--force-tmp', action='store_true',
                        help='allow /tmp and RAM filesystems; default to TMPDIR when --work-dir is omitted')
    parser.add_argument('--_mount-namespace', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.holo_autologin and not args.holo:
        parser.error('--holo-autologin requires --holo')
    if args.rootless and args.format == 'zip':
        parser.error('--rootless requires an img or qcow2 disk image')
    if args.holo and args.recovery and args.format == 'zip':
        parser.error('--holo requires a main system image; ZIP with --recovery contains RecoveryOS only')
    if os.geteuid() != 0:
        parser.error('must run as root on Linux')
    if sys.platform != 'linux':
        parser.error('Linux build host required')
    if not args._mount_namespace:
        require('unshare')
        os.execvp('unshare', ['unshare', '--mount', '--propagation', 'private',
                            sys.executable, str(Path(__file__).resolve()),
                            *sys.argv[1:], '--_mount-namespace'])
    if args.size is None:
        args.size = 20 if args.format == 'qcow2' else 16
    recovery_mib = RECOVERY_MIB if args.recovery else 0
    if args.format != 'zip' and args.size * 1024 <= 513 + args.root_size * 1024 + recovery_mib + 512:
        extra = ' + 2 GiB for RecoveryOS' if args.recovery else ''
        parser.error(f'image too small: need ESP + root{extra} + at least 512 MiB for /home')
    commands = ['debootstrap', 'chroot', 'mount', 'umount', 'git', 'findmnt']
    if args.format != 'zip':
        commands += ['parted', 'losetup', 'udevadm', 'mkfs.vfat', 'mkfs.ext4']
    if args.rootless:
        commands.append('sfdisk')
    if args.format != 'zip' and (args.rootless or args.recovery):
        commands.append('blkid')
    if args.format == 'qcow2':
        commands.append('qemu-img')
    if args.recovery:
        commands.append('mksquashfs')
        if args.format != 'zip':
            commands += ['cp', 'dd']
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
        if args.format == 'zip' and args.recovery:
            make_zip(build_recovery(temp, args), args.output)
        elif args.format == 'zip':
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
