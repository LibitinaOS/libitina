import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_profile import fetch_profile


class ProfileTests(unittest.TestCase):
    def test_download_and_local_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = {'login': '123Cat', 'name': 'Cat Person',
                       'avatar_url': 'https://avatars.githubusercontent.com/u/123'}
            with patch('github_profile.urlopen', side_effect=[
                    io.BytesIO(json.dumps(profile).encode()), io.BytesIO(b'picture')]) as request, \
                    patch('github_profile.tempfile.mkdtemp', return_value=directory):
                result = fetch_profile('123Cat')
            self.assertEqual(result['fullname'], 'Cat Person')
            self.assertEqual(result['username'], 'gh_123cat')
            self.assertEqual(result['github_username'], '123Cat')
            self.assertEqual(Path(result['profile_picture']).read_bytes(), b'picture')
            self.assertEqual(request.call_args_list[0].args[0].full_url,
                             'https://api.github.com/users/123Cat')
            self.assertNotIn('password', result)

    def test_missing_display_name_falls_back_to_login(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = {'login': 'Octocat', 'name': None, 'avatar_url': 'https://example.com/avatar'}
            with patch('github_profile.urlopen', side_effect=[
                    io.BytesIO(json.dumps(profile).encode()), io.BytesIO(b'picture')]), \
                    patch('github_profile.tempfile.mkdtemp', return_value=directory):
                result = fetch_profile('Octocat')
            self.assertEqual(result['fullname'], 'Octocat')
            self.assertEqual(result['username'], 'octocat')

    def test_invalid_username_never_requests_network(self):
        with patch('github_profile.urlopen') as request:
            with self.assertRaises(ValueError):
                fetch_profile('../users')
            request.assert_not_called()

    def test_http_failure_propagates_without_creating_file(self):
        with patch('github_profile.urlopen', side_effect=HTTPError('url', 404, 'Not Found', {}, None)), \
                patch('github_profile.tempfile.mkdtemp') as create:
            with self.assertRaises(HTTPError):
                fetch_profile('missing')
            create.assert_not_called()

    def test_empty_avatar_rejected_before_creating_file(self):
        profile = {'login': 'cat', 'avatar_url': 'https://example.com/avatar'}
        with patch('github_profile.urlopen', side_effect=[
                io.BytesIO(json.dumps(profile).encode()), io.BytesIO(b'')]), \
                patch('github_profile.tempfile.mkdtemp') as create:
            with self.assertRaises(ValueError):
                fetch_profile('cat')
            create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
