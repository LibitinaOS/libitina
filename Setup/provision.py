#!/usr/bin/env python3
"""Root-only consumer of the first-boot wizard export; never run from user input."""
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys

STATE = Path('/var/lib/libitina')
# Outside the private state directory so the unprivileged OOBE can read it.
IN_PROGRESS = Path('/var/lib/libitina-setup-in-progress')
EXPORT = Path('/home/defaultuser0/.config/os-setup/setup.json')
LANGUAGES = {'en_US', 'en_GB', 'es_ES', 'fr_FR', 'de_DE', 'it_IT', 'pt_BR', 'ja_JP', 'zh_CN'}
KEYBOARDS = {'us', 'gb', 'es', 'fr', 'de', 'it', 'jp'}
OOBE_FILES = (
    Path('/etc/lightdm/lightdm.conf.d/90-libitina-oobe.conf'),
    Path('/etc/sudoers.d/libitina-oobe'),
    Path('/usr/share/wayland-sessions/libitina-oobe.desktop'),
)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def mark_provisioning_started():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = STATE / 'oobe-recovery'
    backup.mkdir(mode=0o700, exist_ok=True)
    if not IN_PROGRESS.exists():
        for path in OOBE_FILES:
            shutil.copy2(path, backup / path.name)
    # Include the recovery copies in the durability boundary.
    os.sync()
    # Persist both the file and directory entry before changing the system.
    with IN_PROGRESS.open('w') as marker:
        marker.flush()
        os.fsync(marker.fileno())
    fd = os.open(IN_PROGRESS.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def recover_oobe():
    """Restore the warning session if power was lost during final cleanup."""
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if not IN_PROGRESS.exists() or (STATE / 'setup-complete').exists():
        return
    try:
        pwd.getpwnam('defaultuser0')
    except KeyError:
        run('useradd', '--create-home', '--shell', '/bin/bash', 'defaultuser0')
    for path in OOBE_FILES:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STATE / 'oobe-recovery' / path.name, path)

# todo: the main OOBE program should sanity check each step, make sure it does
def validate(data):
    for key in ('username', 'password', 'fullname', 'hostname', 'language', 'keyboard', 'timezone'):
        if not isinstance(data.get(key), str) or any(c in data[key] for c in '\x00\n\r'):
            raise ValueError(f'Invalid {key}')
    # todo: make sure that other system accounts are here too
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', data['username']) or data['username'] in {'root', 'defaultuser0'}:
        raise ValueError('Invalid or reserved username')
    # todo: these should not be the same check, what does the password have to do with the full name?
    if len(data['password']) < 3 or ':' in data['fullname']:
        raise ValueError('Invalid password or full name')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', data['hostname'], re.IGNORECASE):
        raise ValueError('Invalid hostname')
    if data.get('appearance', 'light') not in ('light', 'dark'):
        raise ValueError('Invalid appearance')
    if data['language'] not in LANGUAGES or data['keyboard'] not in KEYBOARDS:
        raise ValueError('Invalid locale or keyboard')
    zone = Path('/usr/share/zoneinfo') / data['timezone']
    if not zone.resolve().is_relative_to(Path('/usr/share/zoneinfo').resolve()) or not zone.is_file():
        raise ValueError('Invalid timezone')
    return data


def apply_appearance(account, mode):
    dark = mode == 'dark'
    theme = 'DeletescapeUi-Dark' if dark else 'DeletescapeUi'
    home = Path(account.pw_dir)
    directory = home / '.config' / 'gtk-3.0'
    directory.mkdir(parents=True, exist_ok=True)
    settings = directory / 'settings.ini'
    settings.write_text(f'[Settings]\ngtk-theme-name={theme}\n'
                        f'gtk-application-prefer-dark-theme={str(dark).lower()}\n')
    for path in (home / '.config', directory, settings):
        os.chown(path, account.pw_uid, account.pw_gid)
    # Write the user's GSettings database with their own session bus and HOME.
    for key, value in (('gtk-theme', theme),
                       ('color-scheme', 'prefer-dark' if dark else 'default')):
        run('runuser', '-u', account.pw_name, '--', 'env', f'HOME={home}',
            'dbus-run-session', '--', 'gsettings', 'set',
            'org.gnome.desktop.interface', key, value)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if (STATE / 'setup-complete').exists():
        return
    mark_provisioning_started()
    # Stop the unprivileged session before reading its private export, avoiding races.
    run('systemctl', 'stop', 'lightdm.service')
    subprocess.run(['loginctl', 'terminate-user', 'defaultuser0'], check=False)
    fd = os.open(EXPORT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or info.st_uid != pwd.getpwnam('defaultuser0').pw_uid:
            raise ValueError('Invalid setup export')
        data = validate(json.load(stream))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = STATE / 'setup-user'
    username = data['username']
    try:
        pwd.getpwnam(username)
    except KeyError:
        pending.write_text(username)
        run('useradd', '--create-home', '--shell', '/bin/bash', '--groups', 'sudo',
            '--comment', data['fullname'], username)
    else:
        if not pending.exists() or pending.read_text() != username:
            raise ValueError('Account already exists')
    run('chpasswd', input=f"{username}:{data['password']}\n")
    locale = data['language'] + '.UTF-8'
    Path('/etc/locale.gen').write_text(f'{locale} UTF-8\n')
    run('locale-gen')
    run('localectl', 'set-locale', f'LANG={locale}')
    run('timedatectl', 'set-timezone', data['timezone'])
    run('hostnamectl', 'set-hostname', data['hostname'])
    # Debian disables localectl keyboard writes. Configure through the package
    # interface so its maintainer scripts own the on-disk representation.
    run('debconf-set-selections', input=(
        'keyboard-configuration keyboard-configuration/modelcode string pc105\n'
        f'keyboard-configuration keyboard-configuration/layoutcode string {data["keyboard"]}\n'
        'keyboard-configuration keyboard-configuration/variantcode string\n'
        'keyboard-configuration keyboard-configuration/optionscode string\n'))
    run('env', 'DEBIAN_FRONTEND=noninteractive',
        'dpkg-reconfigure', 'keyboard-configuration')
    home = Path(pwd.getpwnam(username).pw_dir)
    session = home / '.dmrc'
    session.write_text('[Desktop]\nSession=yuihirasawa\n')
    account = pwd.getpwnam(username)
    os.chown(session, account.pw_uid, account.pw_gid)
    apply_appearance(account, data.get('appearance', 'light'))
    # Destroy the plaintext password export before removing the temporary home.
    EXPORT.unlink()
    run('userdel', '--remove', 'defaultuser0')
    for path in OOBE_FILES:
        path.unlink(missing_ok=True)
    (STATE / 'setup-complete').touch(mode=0o600)
    pending.unlink(missing_ok=True)
    os.sync()
    IN_PROGRESS.unlink(missing_ok=True)
    run('systemctl', 'reboot')


if __name__ == '__main__':
    if sys.argv[1:] == ['--recover-oobe']:
        recover_oobe()
    else:
        main()
