"""Draft keys follow verified owners, not login or model cache generations."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.app.service import Bridge
from bridge.clients.manager import DesktopSessions
from bridge.clients.claude.remote_worker import draft_account_identity as claude_account_identity
from bridge.features.auth.auth import Auth


class DraftIdentityTests(unittest.TestCase):
    def test_gateway_scope_survives_restart_and_password_rotation(self):
        with tempfile.TemporaryDirectory() as root:
            config = {'mode': 'password', 'username': 'alice', 'salt': 'one', 'hash': 'old'}
            first = Auth(config, root)
            expected = first.draft_identity
            token, _ = first.new_session('127.0.0.1')
            first.logout(token)
            self.assertEqual(Auth(config, root).draft_identity, expected)
            rotated = Auth({**config, 'salt': 'two', 'hash': 'new'}, root)
            self.assertEqual(rotated.draft_identity, expected)
            self.assertNotEqual(Auth({**config, 'username': 'bob'}, root).draft_identity, expected)
            with tempfile.TemporaryDirectory() as other:
                self.assertNotEqual(Auth(config, other).draft_identity, expected)
            for secret in ('old', 'new', first.cookie_name):
                self.assertNotIn(secret, expected)

    def test_codex_owner_is_stable_across_token_and_model_changes(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            bridge = Bridge.__new__(Bridge)
            bridge.host = 'local'
            bridge.codex_home = home
            bridge.accounts = SimpleNamespace(lock=threading.RLock(), active_matches=lambda: False)
            (home/'auth.json').write_text(json.dumps({'tokens': {'account_id': 'account-a', 'access_token': 'old'}}))
            first = bridge._draft_account_identity({'provider': 'openai', 'model': 'first'})
            (home/'auth.json').write_text(json.dumps({'tokens': {'account_id': 'account-a', 'access_token': 'renewed'}}))
            self.assertEqual(bridge._draft_account_identity({'provider': 'openai', 'model': 'second'}), first)
            (home/'auth.json').write_text(json.dumps({'tokens': {'account_id': 'account-b'}}))
            self.assertNotEqual(bridge._draft_account_identity({'provider': 'openai'}), first)
            bridge.host = 'ssh-host'
            bridge.cli_sessions = SimpleNamespace(alias=None)
            self.assertEqual(bridge._draft_account_identity({'provider': 'openai'}), '')
            bridge.host = 'local'
            self.assertEqual(bridge._draft_account_identity({'provider': 'custom'}), '')

    def test_codex_api_credential_fallback_excludes_model(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home/'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'key-one'}))
            bridge = Bridge.__new__(Bridge)
            bridge.host = 'local'
            bridge.codex_home = home
            bridge.accounts = SimpleNamespace(lock=threading.RLock(), active_matches=lambda: False)
            first = bridge._draft_account_identity({'provider': 'openai', 'model': 'a'})
            self.assertEqual(first, bridge._draft_account_identity({'provider': 'openai', 'model': 'b'}))
            (home/'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'key-two'}))
            self.assertNotEqual(first, bridge._draft_account_identity({'provider': 'openai'}))

    def test_codex_ssh_owner_comes_from_selected_remote(self):
        bridge = Bridge.__new__(Bridge)
        bridge.host = 'remote-one'
        bridge.cli_sessions = SimpleNamespace(alias='ssh-one', remote_home=Path('/remote/codex'))
        bridge._draft_remote_lock = threading.Lock()
        bridge._draft_remote_cache = {}
        with patch('bridge.app.service.ssh_read', return_value={'identity': 'remote-account'}) as read:
            first = bridge._draft_account_identity({'provider': 'openai'})
            self.assertEqual(bridge._draft_account_identity({'provider': 'openai'}), first)
            read.assert_called_once()
        self.assertEqual(read.call_args.args[0], 'ssh-one')
        self.assertIn('request["home"]', read.call_args.args[1])
        bridge.host = 'remote-two'
        with patch('bridge.app.service.ssh_read', return_value={'identity': 'remote-account'}):
            self.assertNotEqual(bridge._draft_account_identity({'provider': 'openai'}), first)

    def test_claude_native_metadata_survives_credential_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)/'.claude'
            directory.mkdir()
            metadata = {'oauthAccount': {'accountUuid': 'account-one', 'organizationUuid': 'org-one'}}
            (directory/'.claude.json').write_text(json.dumps(metadata))
            first = claude_account_identity(directory)
            self.assertEqual(len(first), 64)
            (directory/'.credentials.json').write_text('{"accessToken":"renewed"}')
            self.assertEqual(claude_account_identity(directory), first)
            metadata['oauthAccount']['accountUuid'] = 'account-two'
            (directory/'.claude.json').write_text(json.dumps(metadata))
            self.assertNotEqual(claude_account_identity(directory), first)

    def test_desktop_uses_verified_deepseek_account_and_fails_closed(self):
        manager = DesktopSessions.__new__(DesktopSessions)
        manager.adapters = {'deepseek': SimpleNamespace()}
        snapshot = {'accounts': [{'id': 'saved-api', 'kind': 'api', 'saved': True}],
                    'activeIds': ['saved-api']}
        manager._account_store = lambda provider: SimpleNamespace(lock=threading.RLock(), public=lambda: snapshot)
        detail = {'session': {'model': 'deepseek-official/model-a'}}
        first = manager._draft_account_identity('deepseek', 'session', detail)
        self.assertEqual(len(first), 64)
        detail['session']['model'] = 'deepseek-official/model-b'
        self.assertEqual(manager._draft_account_identity('deepseek', 'session', detail), first)
        snapshot['activeIds'] = []
        self.assertEqual(manager._draft_account_identity('deepseek', 'session', detail), '')
        snapshot['activeIds'] = ['saved-api']
        self.assertEqual(manager._draft_account_identity('deepseek', 'session', {'session': {'model': 'other/model'}}), '')

    def test_claude_cli_requires_matching_managed_profile(self):
        manager = DesktopSessions.__new__(DesktopSessions)
        profile = {'origin': 'gateway:saved-api:/profile:settings-a', 'directory': '/profile'}
        cli = SimpleNamespace(lock=threading.RLock(), index={'session': {'origin': profile['origin']}},
                              profile_resolver=lambda: profile)
        remote = SimpleNamespace(lock=threading.RLock(), index={})
        manager.adapters = {'claude': SimpleNamespace(cli=cli, remote_cli=remote, index={})}
        access = SimpleNamespace(lock=threading.RLock(), index={'activeClaude': 'saved-api',
            'claude': [{'id': 'saved-api', 'kind': 'api', 'originBase': 'gateway:saved-api:/profile'}]})
        manager.bridge = SimpleNamespace(cli_access=access)
        first = manager._draft_account_identity('claude', 'session', {'session': {}})
        self.assertEqual(len(first), 64)
        profile['origin'] = 'gateway:saved-api:/profile:settings-b'
        cli.index['session']['origin'] = profile['origin']
        self.assertEqual(manager._draft_account_identity('claude', 'session', {'session': {}}), first)
        remote.index['session'] = {'host': 'remote'}
        self.assertEqual(manager._draft_account_identity('claude', 'session', {'session': {}}), '')
        remote.index.clear()
        access.index['activeClaude'] = 'different'
        self.assertEqual(manager._draft_account_identity('claude', 'session', {'session': {}}), '')


if __name__ == '__main__':
    unittest.main()
