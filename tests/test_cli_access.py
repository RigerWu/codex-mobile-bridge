import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.accounts.cli_access import CliAccess
from bridge.clients.claude.cli_session import ClaudeCli, ResumePreflightBlocked


class CliAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = self.root/'native'
        self.native.mkdir()
        self.profile = {'binary': '/fixture/claude', 'directory': self.native,
                        'origin': 'default:'+str(self.native)+':none'}
        self.accounts = SimpleNamespace(public=Mock(return_value={
            'accounts': [], 'activeId': None, 'activeVerified': False,
            'switch': {}, 'blockers': []}))
        self.bridge = SimpleNamespace(data_dir=self.root, accounts=self.accounts)
        self.profile_patch = patch('bridge.features.accounts.cli_access._profile', return_value=self.profile)
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)
        self.manager = CliAccess(self.bridge)

    def save_api(self, **changes):
        request = {'provider': 'claude', 'action': 'saveApi', 'name': 'Claude API',
                   'baseUrl': 'https://fixture.invalid', 'model': 'test-model',
                   'apiKey': 'private-fixture-key', 'authScheme': 'bearer', **changes}
        return self.manager.change(request)['savedId']

    def sync_patch(self, instance):
        module = types.ModuleType('bridge.features.accounts.cli_sync')
        module.LocalCliSync = Mock(return_value=instance)
        return patch.dict(sys.modules, {'bridge.features.accounts.cli_sync': module})

    def test_api_key_is_private_and_edit_without_reentering_key(self):
        identifier = self.save_api()
        directory = self.root/'cli-access'/'claude'/identifier
        self.assertEqual(json.loads((directory/'api.json').read_text())['key'], 'private-fixture-key')
        self.assertEqual((directory/'api.json').stat().st_mode & 0o077, 0)
        self.assertFalse((self.native/'settings.json').exists())
        self.assertNotIn('private-fixture-key', json.dumps(self.manager.public()))
        self.assertNotIn('private-fixture-key', (self.root/'cli-access'/'index.json').read_text())
        self.save_api(id=identifier, name='Updated', apiKey='')
        self.assertEqual(self.manager.public()['claude']['accounts'][0]['name'], 'Updated')
        self.assertEqual(self.manager.public()['claude']['accounts'][0]['authScheme'], 'bearer')
        self.assertEqual(json.loads((directory/'api.json').read_text())['key'], 'private-fixture-key')
        self.assertEqual(self.manager.index['claude'][0]['directory'], str(self.native))

    def test_switch_keeps_native_directory_and_requires_verification(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured', 'authentication': 'configured'}
        with self.sync_patch(sync):
            result = self.manager.change({'provider': 'claude', 'action': 'switch',
                                          'id': identifier, 'confirmed': True})
        self.assertEqual(result['claude']['activeId'], identifier)
        self.assertEqual(self.manager.claude_profile()['directory'], self.native)
        self.assertTrue(self.manager.claude_profile()['managedApi'])
        self.assertEqual(result['claude']['verification']['status'], 'configured')
        sync.apply.assert_called_once()
        sync.verify.assert_called_once()

    def test_native_import_keeps_original_directory_and_history(self):
        history = self.native/'history.jsonl'
        history.write_text('original history')
        with patch('bridge.features.accounts.cli_access._auth_ready', return_value=True):
            self.manager.change({'provider': 'claude', 'action': 'importCurrent',
                                 'name': 'Original Claude'})
        row = self.manager.index['claude'][0]
        self.assertEqual(row['directory'], str(self.native.resolve()))
        self.assertEqual(history.read_text(), 'original history')
        self.assertEqual(sorted(path.name for path in self.native.iterdir()), ['history.jsonl'])
        self.assertEqual(self.manager.control({'action': 'list'})['claude']['accounts'][0]['kind'], 'native')
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        with self.sync_patch(sync):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': row['id'], 'confirmed': True})
        self.assertEqual(self.manager.claude_profile(), self.profile)

    def test_failed_verification_restores_and_keeps_selected_profile(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.side_effect = ValueError('verification failed')
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'verification failed'):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': identifier, 'confirmed': True})
        self.assertIsNone(self.manager.public()['claude']['activeId'])
        self.assertEqual(self.manager.claude_profile(), self.profile)
        sync.restore.assert_called_once()

    def test_busy_preflight_rejects_without_mutating_selection(self):
        identifier = self.save_api()
        sync = Mock()
        sync.preflight.side_effect = ValueError('busy')
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, 'busy'):
            self.manager.change({'provider': 'claude', 'action': 'switch',
                                 'id': identifier, 'confirmed': True})
        self.assertIsNone(self.manager.public()['claude']['activeId'])
        sync.snapshot.assert_not_called()

    def test_claude_switch_does_not_invert_cli_and_profile_locks(self):
        identifier = self.save_api()
        cli = ClaudeCli(profile_resolver=self.manager.claude_profile)
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        class ObservedLock:
            def __init__(self):
                self.inner = threading.RLock()
                self.switch_attempted = threading.Event()
            def __enter__(self):
                if threading.current_thread().name == 'switch':
                    self.switch_attempted.set()
                self.inner.acquire()
                return self
            def __exit__(self, *_):
                self.inner.release()
        cli.lock = ObservedLock()
        holding = threading.Event()
        release = threading.Event()
        poll_done = threading.Event()
        switch_done = threading.Event()
        failures = []
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured'}
        def poll():
            try:
                with cli.lock:
                    holding.set()
                    assert release.wait(2)
                    self.manager.claude_profile()
            except Exception as error:
                failures.append(error)
            finally:
                poll_done.set()
        def switch():
            try:
                self.manager.change({'provider': 'claude', 'action': 'switch',
                                     'id': identifier, 'confirmed': True})
            except Exception as error:
                failures.append(error)
            finally:
                switch_done.set()
        with self.sync_patch(sync):
            poll_thread = threading.Thread(target=poll, daemon=True)
            switch_thread = threading.Thread(target=switch, name='switch', daemon=True)
            poll_thread.start()
            self.assertTrue(holding.wait(2))
            switch_thread.start()
            self.assertTrue(cli.lock.switch_attempted.wait(2))
            release.set()
            self.assertTrue(poll_done.wait(2), 'poll deadlocked resolving profile')
            self.assertTrue(switch_done.wait(2), 'switch deadlocked behind CLI lock')
        self.assertFalse(failures)

    def test_resume_waits_until_claude_switch_transaction_finishes(self):
        identifier = self.save_api()
        cli = ClaudeCli(profile_resolver=self.manager.claude_profile)
        self.bridge.desktop_sessions = SimpleNamespace(adapters={'claude': SimpleNamespace(cli=cli)})
        applying = threading.Event()
        finish_apply = threading.Event()
        switch_done = threading.Event()
        resume_done = threading.Event()
        failures = []
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        sync.verify.return_value = {'status': 'configured'}
        def apply(*_):
            applying.set()
            assert finish_apply.wait(2)
        sync.apply.side_effect = apply
        def switch():
            try:
                self.manager.change({'provider': 'claude', 'action': 'switch',
                                     'id': identifier, 'confirmed': True})
            except Exception as error:
                failures.append(error)
            finally:
                switch_done.set()
        def resume():
            try:
                cli.call('resume', 'old-session', {})
            except ResumePreflightBlocked:
                pass  # The old origin is stale after the completed switch.
            except Exception as error:
                failures.append(error)
            finally:
                resume_done.set()
        with self.sync_patch(sync), patch.object(cli, '_get', return_value={
                'nativeId': 'old-session', 'origin': 'old-origin'}), \
                patch.object(cli, '_public', return_value={'permissionMode': 'default'}):
            switch_thread = threading.Thread(target=switch, daemon=True)
            switch_thread.start()
            self.assertTrue(applying.wait(2))
            resume_thread = threading.Thread(target=resume, daemon=True)
            resume_thread.start()
            self.assertFalse(resume_done.wait(.1), 'resume crossed in-progress settings write')
            finish_apply.set()
            self.assertTrue(switch_done.wait(2))
            self.assertTrue(resume_done.wait(2))
        self.assertFalse(failures)

    def test_codex_stopped_desktop_uses_same_saved_row_and_verifies(self):
        row = {'id': 'saved', 'kind': 'api', 'name': 'Codex API'}
        self.accounts.row = Mock(return_value=row)
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': False}
        with self.sync_patch(sync):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        sync.apply.assert_called_once_with('codex', row)
        sync.verify.assert_called_once_with('codex', row)

    def test_codex_running_desktop_requires_explicit_restart_confirmation(self):
        self.accounts.row = Mock(return_value={'id': 'saved', 'kind': 'api'})
        sync = Mock()
        sync.preflight.return_value = {'desktopRunning': True}
        with self.sync_patch(sync), self.assertRaisesRegex(ValueError, '重启'):
            self.manager.change({'provider': 'codex', 'action': 'switch',
                                 'id': 'saved', 'confirmed': True})
        sync.snapshot.assert_not_called()
        self.assertIsNone(self.manager.codex_switch)


if __name__ == '__main__':
    unittest.main()
