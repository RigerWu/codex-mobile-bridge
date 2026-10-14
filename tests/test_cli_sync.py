import json
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.accounts.accounts import Accounts, private_bytes, private_json
from bridge.features.accounts.cli_sync import LocalCliSync


class LocalCliSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'codex'
        self.home.mkdir()
        (self.home/'config.toml').write_text('model="before"\n')
        (self.home/'auth.json').write_text('{"OPENAI_API_KEY":"before"}')
        client = SimpleNamespace(socket_path=str(self.root/'missing.sock'))
        cli = SimpleNamespace(lock=threading.RLock(), joined={}, attaching=set(), requests={}, client=client)
        self.bridge = SimpleNamespace(codex_home=self.home, data_dir=self.root,
            catalog_reader=SimpleNamespace(executable=self.root/'codex-bin'), remote_bridges={},
            live={}, submissions={}, lock=threading.RLock(), cli_sessions=cli)
        self.accounts = Accounts(self.bridge)
        self.bridge.accounts = self.accounts
        self.sync = LocalCliSync(self.bridge)

    def test_stopped_desktop_switch_uses_saved_files_and_restores_on_failure(self):
        row = self.accounts.add_api({'name': 'Fixture', 'baseUrl': 'https://api.example/v1',
                                     'apiKey': 'fixture-key', 'model': 'fixture-model'})['accounts'][-1]
        before = self.accounts.snapshot_files()
        def prepare(saved, original):
            folder = self.root/'prepared'
            folder.mkdir(exist_ok=True)
            private_bytes(folder/'config.toml', b'model_provider="bridge_api"\nmodel="fixture-model"\n')
            private_json(folder/'auth.json', {'OPENAI_API_KEY': 'fixture-key'})
            return folder
        self.accounts.prepare = prepare
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop, \
             patch.object(self.sync, 'verify', side_effect=ValueError('native mismatch')):
            desktop.return_value.processes.return_value = []
            with self.assertRaisesRegex(ValueError, 'native mismatch'):
                self.sync.apply('codex', row)
        self.assertEqual(self.accounts.snapshot_files(), before)
        self.assertIsNone(self.accounts.index.get('activeId'))
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop, \
             patch.object(self.sync, 'verify', return_value={'status': 'verified'}):
            desktop.return_value.processes.return_value = []
            self.assertEqual(self.sync.apply('codex', row)['status'], 'applied')
        self.assertEqual(self.accounts.index['activeId'], row['id'])
        self.assertEqual(json.loads((self.home/'auth.json').read_text())['OPENAI_API_KEY'], 'fixture-key')

    def test_live_codex_daemon_blocks_shared_config_mutation(self):
        path = self.root/'live.sock'
        path.touch()
        self.bridge.cli_sessions.client.socket_path = str(path)
        with patch('bridge.features.accounts.cli_sync.socket.socket') as probe, \
             patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop:
            desktop.return_value.processes.return_value = []
            with self.assertRaisesRegex(ValueError, '后台服务'):
                self.sync.preflight('codex')
            probe.return_value.__enter__.return_value.connect.assert_called_once_with(str(path))

    def test_managed_claude_api_is_materialized_and_restorable(self):
        identifier = 'a'*32
        directory = self.root/'native-claude'
        directory.mkdir()
        private_json(directory/'settings.json', {'hooks': {'PreToolUse': []},
            'env': {'UNRELATED': 'keep', 'ANTHROPIC_API_KEY': 'native-key'}})
        original = (directory/'settings.json').read_bytes()
        secret = self.root/'cli-access'/'claude'/identifier/'api.json'
        private_json(secret, {'key': 'fixture-key'})
        row = {'id': identifier, 'kind': 'api', 'directory': str(directory),
               'secretPath': str(secret),
               'baseUrl': 'https://api.example', 'model': 'fixture-model', 'authScheme': 'bearer'}
        backup = self.sync.snapshot('claude')
        self.sync.apply('claude', row)
        with patch.object(self.sync, '_claude_auth', return_value={'loggedIn': True}) as auth:
            self.assertEqual(self.sync.verify('claude', row)['status'], 'configured')
        auth.assert_called_once_with(directory.resolve(), 'oauth_token')
        settings = json.loads((directory/'settings.json').read_text())
        self.assertEqual(settings['env']['ANTHROPIC_AUTH_TOKEN'], 'fixture-key')
        self.assertNotIn('ANTHROPIC_API_KEY', settings['env'])
        self.assertEqual(settings['env']['UNRELATED'], 'keep')
        self.assertEqual(settings['hooks'], {'PreToolUse': []})
        self.sync.restore('claude', backup)
        self.assertEqual((directory/'settings.json').read_bytes(), original)

    def test_claude_switch_closes_only_idle_gateway_owned_processes(self):
        idle = Mock()
        idle.process.poll.return_value = None
        idle.condition = threading.Condition()
        idle.active = False
        idle.pending = {}
        active = Mock()
        active.process.poll.return_value = None
        active.condition = threading.Condition()
        active.active = True
        active.pending = {}
        cli = SimpleNamespace(lock=threading.RLock(), owned={'idle': idle, 'active': active}, auth_cache='cached')
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        with self.assertRaisesRegex(ValueError, '先结束'):
            self.sync.preflight('claude')
        with self.assertRaisesRegex(ValueError, '先结束'):
            self.sync._close_claude_idle()
        idle.close.assert_not_called()
        active.close.assert_not_called()
        cli.owned.pop('active')
        self.sync.preflight('claude')
        self.sync._close_claude_idle()
        idle.close.assert_called_once_with()
        self.assertFalse(cli.owned)
        self.assertIsNone(cli.auth_cache)

    def test_first_api_switch_creates_missing_default_profile_then_rolls_back(self):
        identifier = 'b'*32
        directory = self.root/'.claude'
        secret = self.root/'cli-access'/'claude'/identifier/'api.json'
        private_json(secret, {'key': 'fixture-key'})
        row = {'id': identifier, 'kind': 'api', 'directory': str(directory),
               'secretPath': str(secret), 'baseUrl': 'https://fixture.invalid',
               'model': 'fixture-model', 'authScheme': 'x-api-key'}
        native = {'directory': directory, 'origin': 'default:'+str(directory)+':none',
                  'binary': '/fixture/claude'}
        with patch('bridge.features.accounts.cli_sync.Path.home', return_value=self.root), \
             patch('bridge.clients.claude.cli_session._profile', return_value=native), \
             patch.object(self.sync, '_claude_auth', return_value={'loggedIn': True}):
            self.assertEqual(self.sync.apply('claude', row)['status'], 'applied')
            self.assertTrue((directory/'settings.json').is_file())
            self.assertEqual(self.sync.verify('claude', row)['status'], 'configured')
            self.sync.restore('claude', {})
        self.assertFalse(directory.exists())
        row['directory'] = str(self.root/'different-missing')
        with self.assertRaisesRegex(ValueError, '配置目录不可用'):
            self.sync.apply('claude', row)

    def test_desktop_claude_import_requires_active_verified_api_snapshot(self):
        directory = self.root/'desktop-profile'
        directory.mkdir()
        config = {'inferenceGatewayBaseUrl': 'https://gateway.example',
                  'inferenceGatewayApiKey': 'fixture-secret',
                  'inferenceGatewayAuthScheme': 'x-api-key',
                  'inferenceModels': ['fixture-model']}
        digest = hashlib.sha256(json.dumps(config, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
        private_json(directory/'gateway.json', {'config': config})
        private_json(directory/'manifest.json', {'fingerprint': digest})
        store = Mock()
        store.public.return_value = {'activeId': 'desktop-api', 'current': {'kind': 'api'}}
        store._account.return_value = ({'id': 'desktop-api', 'kind': 'api', 'name': 'Desktop API',
                                         'fingerprint': digest}, directory)
        imported = self.sync.import_desktop_claude('desktop-api', store)
        self.assertEqual(imported['authScheme'], 'x-api-key')
        self.assertEqual(imported['model'], 'fixture-model')
        store.public.return_value = {'activeId': 'other', 'current': {'kind': 'official'}}
        with self.assertRaisesRegex(ValueError, '官方登录不能复制'):
            self.sync.import_desktop_claude('desktop-api', store)


if __name__ == '__main__':
    unittest.main()
