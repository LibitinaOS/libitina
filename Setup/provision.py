#!/usr/bin/env python3
"""Root-only consumer of the first-boot wizard export; never run from user input."""
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

STATE = Path('/var/lib/libitina')
EXPORT = Path('/home/defaultuser0/.config/os-setup/setup.json')
LANGUAGES = {'en_US', 'en_GB', 'es_ES', 'fr_FR', 'de_DE', 'it_IT', 'pt_BR', 'ja_JP', 'zh_CN'}
KEYBOARDS = {'us', 'gb', 'es', 'fr', 'de', 'it', 'jp'}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def validate(data):
    for key in ('username', 'password', 'fullname', 'hostname', 'language', 'keyboard', 'timezone'):
        if not isinstance(data.get(key), str) or any(c in data[key] for c in '\x00\n\r'):
            raise ValueError(f'Invalid {key}')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', data['username']) or data['username'] in {'root', 'defaultuser0'}:
        raise ValueError('Invalid or reserved username')
    if len(data['password']) < 8 or ':' in data['fullname']:
        raise ValueError('Invalid password or full name')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', data['hostname']):
        raise ValueError('Invalid hostname')
    if data['language'] not in LANGUAGES or data['keyboard'] not in KEYBOARDS:
        raise ValueError('Invalid locale or keyboard')
    zone = Path('/usr/share/zoneinfo') / data['timezone']
    if not zone.resolve().is_relative_to(Path('/usr/share/zoneinfo').resolve()) or not zone.is_file():
        raise ValueError('Invalid timezone')
    return data


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if (STATE / 'setup-complete').exists():
        return
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
    # Destroy the plaintext password export before removing the temporary home.
    EXPORT.unlink()
    run('userdel', '--remove', 'defaultuser0')
    for path in ('/etc/lightdm/lightdm.conf.d/90-libitina-oobe.conf',
                 '/etc/sudoers.d/libitina-oobe',
                 '/usr/share/wayland-sessions/libitina-oobe.desktop'):
        Path(path).unlink(missing_ok=True)
    (STATE / 'setup-complete').touch(mode=0o600)
    pending.unlink(missing_ok=True)
    run('systemctl', 'reboot')


if __name__ == '__main__':
    main()
