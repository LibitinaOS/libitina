"""Fetch public GitHub profile details without requesting credentials."""
import json
import re
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


def fetch_profile(username):
    """Return setup fields; profile_picture is a provider-independent local path.

    The private temporary directory intentionally outlives the wizard because the
    configuration references it. A future installer must copy it before temp cleanup.
    """
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?', username):
        raise ValueError('Invalid GitHub username.')
    request = Request(f'https://api.github.com/users/{username}', headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'LibitinaOS-Setup',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with urlopen(request, timeout=15) as response:
        profile = json.loads(response.read(1024 * 1024))
    login = profile['login']
    if not isinstance(login, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,39}', login):
        raise ValueError('GitHub returned an invalid username.')
    avatar_url = profile['avatar_url']
    if not isinstance(avatar_url, str) or not avatar_url.startswith('https://'):
        raise ValueError('GitHub returned an invalid picture URL.')
    with urlopen(Request(avatar_url, headers={'User-Agent': 'LibitinaOS-Setup'}), timeout=15) as response:
        image = response.read(5 * 1024 * 1024 + 1)
    if not image or len(image) > 5 * 1024 * 1024:
        raise ValueError('Profile picture is empty or exceeds 5 MiB.')
    directory = Path(tempfile.mkdtemp(prefix='libitina-profile-'))
    picture = directory / 'avatar'
    try:
        picture.write_bytes(image)
        picture.chmod(0o600)
    except OSError:
        picture.unlink(missing_ok=True)
        directory.rmdir()
        raise
    # GitHub permits uppercase and digit-leading handles; local accounts do not.
    local_username = login.lower()
    if not re.match(r'[a-z_]', local_username):
        local_username = 'gh_' + local_username
    return {
        'account_source': 'github', 'github_username': login,
        'fullname': profile.get('name') or login, 'username': local_username,
        'profile_picture': str(picture),
    }
