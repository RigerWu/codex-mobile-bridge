import tempfile
import json
import os
import shlex
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnsupported, AppServerUnknownResult
from bridge.clients.codex.cli_session import CliSessions, _local_cli, _managed_cli, _ssh_daemon_start_command, _ssh_proxy_command

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self, home):
        self.socket_path = str(home / 'app-server-control/app-server-control.sock')
        self.process = object()
        self.sent = []
        self.replies = []
        self.loaded = True
        self.thread = {'id': 'thread-a', 'cwd': '/original/project',
                       'modelProvider': 'original-provider', 'model': 'original-model',
                       'status': {'type': 'idle'}, 'turns': [], 'name': 'CLI task'}

    def connect(self):
        return {'codexHome': str(Path(self.socket_path).parents[1])}

    def is_loaded(self, thread_id):
        return self.loaded

    def request(self, method, params, **kwargs):
        self.sent.append((method, params))
        if method == 'thread/read':
            return {'thread': self.thread.copy()}
        if method == 'thread/resume':
            return {'thread': self.thread.copy(), 'modelProvider': 'original-provider',
                    'cwd': '/original/project', 'model': 'original-model'}
        if method == 'turn/start':
            return {'turn': {'id': 'turn-1'}}
        return {}

    def respond(self, request_id, result):
        self.replies.append((request_id, result))

    def close(self):
        self.process = None


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.home = Path(self.temp.name)
        (self.home / 'app-server-control').mkdir()
        (self.home / 'app-server-control/app-server-control.sock').touch()
        self.states = []
        self.disconnections = []
        self.sessions = CliSessions(self.home, lambda tid, state: self.states.append((tid, state)),
                                    lambda tid=None: self.disconnections.append(tid))
        self.fake = FakeClient(self.home)
        self.sessions.client = self.fake

    def tearDown(self):
        self.temp.cleanup()

    def test_passive_attach_does_not_connect_or_resume(self):
        self.sessions._start_daemon = Mock()
        Path(self.fake.socket_path).unlink()
        self.assertIsNone(self.sessions.attach('thread-a'))
        self.assertEqual(self.fake.sent, [])
        self.sessions._start_daemon.assert_not_called()

    def test_unavailable_proxy_starts_daemon_only_for_explicit_attach(self):
        info = {'codexHome': str(self.home)}
        self.fake.connect = Mock(side_effect=[AppServerUnavailable('offline'),
                                              AppServerUnavailable('offline'), info])
        with patch.object(self.sessions, '_start_daemon') as start:
            self.assertEqual(self.sessions.attach('thread-a', explicit=True)['id'], 'thread-a')
        start.assert_called_once_with()
        self.assertEqual(self.fake.connect.call_count, 3)

    def test_explicit_attach_resumes_saved_thread_and_preserves_identity(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/original/project',
                 'model_provider': 'original-provider'}
        with patch.object(self.sessions.store, 'get', return_value=saved):
            state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['modelProvider'], 'original-provider')
        self.assertEqual(state['cwd'], '/original/project')
        self.assertEqual([params for method, params in self.fake.sent if method == 'thread/resume'],
                         [{'threadId': 'thread-a', 'excludeTurns': True}])
        self.assertFalse(any('model' in params or 'cwd' in params or 'sandbox' in params
                             for method, params in self.fake.sent if method == 'thread/resume'))

    def test_cold_resume_rejects_saved_identity_mismatch(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/other/project',
                 'model_provider': 'original-provider'}
        with patch.object(self.sessions.store, 'get', return_value=saved):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))

    def test_resume_response_identity_mismatch_is_not_joined(self):
        self.fake.loaded = False
        saved = {'id': 'thread-a', 'origin': 'cli', 'cwd': '/original/project',
                 'model_provider': 'original-provider'}
        original = self.fake.request
        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                result['modelProvider'] = 'different-provider'
            return result
        self.fake.request = request
        with patch.object(self.sessions.store, 'get', return_value=saved):
            with self.assertRaises(AppServerUnsupported):
                self.sessions.attach('thread-a', explicit=True)
        self.assertNotIn('thread-a', self.sessions.joined)

    def test_loaded_thread_preserves_identity(self):
        state = self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(state['modelProvider'], 'original-provider')
        self.assertEqual(state['cwd'], '/original/project')
        self.assertEqual([params for method, params in self.fake.sent if method == 'thread/resume'],
                         [{'threadId': 'thread-a', 'excludeTurns': True}])

    def test_core_turn_and_approval_mapping(self):
        self.sessions.attach('thread-a', explicit=True)
        result = self.sessions.call('thread-a', 'thread-follower-start-turn', {
            'turnStart': {'request': {'input': [{'type': 'text', 'text': 'Inspect this'}],
                                      'clientUserMessageId': 'submission-1'},
                          'context': {'fileAttachments': [{'path': '/original/project/a.py'}]}}})
        self.assertEqual(result['turn']['id'], 'turn-1')
        params = next(params for method, params in self.fake.sent if method == 'turn/start')
        self.assertIn('/original/project/a.py', params['input'][0]['text'])
        self.assertIn('Inspect this', params['input'][0]['text'])
        self.assertNotIn('model', params)
        self.sessions._server_request({'id': 9, 'method': 'item/commandExecution/requestApproval',
                                       'params': {'threadId': 'thread-a', 'command': 'echo hello'}})
        self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                           {'requestId': 9, 'decision': 'decline'})
        self.assertEqual(self.fake.replies, [(9, {'decision': 'decline'})])
        self.sessions._server_request({'id': 10, 'method': 'item/tool/requestUserInput',
                                       'params': {'threadId': 'thread-a', 'questions': []}})
        self.sessions.call('thread-a', 'thread-follower-submit-user-input',
                           {'requestId': 10, 'response': {'answers': {'q': {'answers': ['yes']}}}})
        self.assertEqual(self.fake.replies[-1][0], 10)

    def test_unsupported_operation_fails_closed(self):
        self.sessions.attach('thread-a', explicit=True)
        with self.assertRaises(AppServerUnsupported):
            self.sessions.call('thread-a', 'thread-follower-update-thread-settings', {})

    def test_live_events_update_state_and_resolve_pending_without_refresh(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'turn/started', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'inProgress', 'items': []}}})
        self.sessions._event({'method': 'item/started', 'params': {
            'threadId': 'thread-a', 'turnId': 'turn-1',
            'item': {'id': 'msg-1', 'type': 'agentMessage', 'text': ''}}})
        self.sessions._event({'method': 'item/agentMessage/delta', 'params': {
            'threadId': 'thread-a', 'turnId': 'turn-1', 'itemId': 'msg-1', 'delta': 'hello'}})
        self.assertEqual(self.states[-1][1]['turns'][0]['items'][0]['text'], 'hello')
        self.sessions._server_request({'id': 42, 'method': 'item/fileChange/requestApproval',
                                       'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}})
        self.assertEqual(self.states[-1][1]['requests'][0]['id'], 42)
        self.sessions._event({'method': 'serverRequest/resolved', 'params': {
            'threadId': 'thread-a', 'requestId': 42}})
        self.assertEqual(self.states[-1][1]['requests'], [])
        self.sessions._event({'method': 'turn/completed', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'completed'}}})
        self.assertEqual(self.states[-1][1]['turns'][0]['status'], 'completed')

    def test_completed_turn_keeps_user_item_omitted_from_event(self):
        self.fake.thread['turns'] = [{'id': 'turn-1', 'status': 'inProgress', 'items': [
            {'id': 'user-1', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Hello'}]},
            {'id': 'reasoning-1', 'type': 'reasoning', 'summary': 'Draft'},
            {'id': 'assistant-1', 'type': 'agentMessage', 'text': 'Partial'}]}]
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'turn/completed', 'params': {
            'threadId': 'thread-a', 'turn': {'id': 'turn-1', 'status': 'completed', 'items': [
                {'id': 'assistant-1', 'type': 'agentMessage', 'text': 'Final'}]}}})
        items = self.states[-1][1]['turns'][0]['items']
        self.assertEqual([(item['id'], item['type']) for item in items],
                         [('user-1', 'userMessage'), ('assistant-1', 'agentMessage')])
        self.assertEqual(items[1]['text'], 'Final')

    def test_unload_revokes_write_access(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._event({'method': 'thread/closed', 'params': {'threadId': 'thread-a'}})
        self.assertEqual(self.disconnections, ['thread-a'])
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-interrupt-turn',
                               {'expectedTurnId': 'turn-1'})

    def test_unloading_one_thread_keeps_another_joined(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['id'] = 'thread-b'
        self.sessions.attach('thread-b', explicit=True)
        self.sessions._event({'method': 'thread/closed', 'params': {'threadId': 'thread-a'}})
        self.assertEqual(self.disconnections, ['thread-a'])
        self.assertNotIn('thread-a', self.sessions.joined)
        self.assertIn('thread-b', self.sessions.joined)
        self.assertEqual(self.sessions.call('thread-b', 'thread-follower-interrupt-turn',
                                            {'expectedTurnId': 'turn-2'}), {})
        self.assertIn(('turn/interrupt', {'threadId': 'thread-b', 'turnId': 'turn-2'}), self.fake.sent)

    def test_provider_change_revokes_write_access(self):
        self.sessions.attach('thread-a', explicit=True)
        self.fake.thread['modelProvider'] = 'another-provider'
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-interrupt-turn',
                               {'expectedTurnId': 'turn-1'})
        self.assertFalse(any(method == 'turn/interrupt' for method, _ in self.fake.sent))

    def test_unknown_approval_result_quarantines_numeric_request_id(self):
        self.sessions.attach('thread-a', explicit=True)
        request = {'id': 73, 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}}
        self.sessions._server_request(request)
        sends = []
        def uncertain(request_id, result):
            sends.append((request_id, result))
            raise AppServerUnknownResult('response may have arrived')
        self.fake.respond = uncertain
        with self.assertRaises(AppServerUnknownResult):
            self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                               {'requestId': 73, 'decision': 'decline'})
        self.assertEqual(self.states[-1][1]['requests'], [])
        self.sessions._server_request(request)
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-command-approval-decision',
                               {'requestId': 73, 'decision': 'decline'})
        self.assertEqual(sends, [(73, {'decision': 'decline'})])

    def test_concurrent_approval_claim_allows_only_one_send(self):
        self.sessions.attach('thread-a', explicit=True)
        self.sessions._server_request({'id': 74, 'method': 'item/fileChange/requestApproval',
                                       'params': {'threadId': 'thread-a', 'turnId': 'turn-1'}})
        entered = threading.Event()
        release = threading.Event()
        sends = []
        def wait_for_result(request_id, result):
            sends.append(request_id)
            entered.set()
            release.wait(2)
        self.fake.respond = wait_for_result
        worker = threading.Thread(target=lambda: self.sessions.call(
            'thread-a', 'thread-follower-file-approval-decision',
            {'requestId': 74, 'decision': 'accept'}))
        worker.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaises(AppServerUnavailable):
            self.sessions.call('thread-a', 'thread-follower-file-approval-decision',
                               {'requestId': 74, 'decision': 'accept'})
        release.set()
        worker.join(2)
        self.assertEqual(sends, [74])

    def test_ssh_proxy_uses_strict_existing_runtime_connection(self):
        command = _ssh_proxy_command('saved-host')
        self.assertIn('BatchMode=yes', command)
        self.assertIn('ClearAllForwardings=yes', command)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertEqual(command[-2], 'saved-host')
        self.assertIn('codex app-server proxy', command[-1])
        self.assertTrue(command[-1].startswith('exec "$SHELL" -lc '))
        self.assertEqual(shlex.split(command[-1])[2], '-lc')
        self.assertIn('exec codex app-server proxy', shlex.split(command[-1])[3])
        with self.assertRaises(ValueError):
            _ssh_proxy_command('-unsafe')
        remote = CliSessions(self.home / 'local-only-path', lambda *_: None,
                             lambda: None, alias='saved-host')
        remote.client = FakeClient(self.home)
        self.assertTrue(remote.available())
        self.assertEqual(remote.attach('thread-a', explicit=True)['id'], 'thread-a')

    def test_ssh_proxy_falls_back_to_selected_managed_package_without_path_cli(self):
        managed = self.home / 'packages/app-server-daemon/current/bin/codex'
        managed.parent.mkdir(parents=True)
        managed.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        managed.chmod(0o700)
        inner = shlex.split(_ssh_proxy_command('saved-host')[-1])[3]
        environment = os.environ.copy()
        environment.update(HOME=str(self.home), CODEX_HOME=str(self.home), PATH='')
        result = subprocess.run(['/bin/sh', '-c', inner], env=environment,
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['app-server', 'proxy'])

    def test_packaged_app_finds_existing_daemon_cli_without_path(self):
        runtime = self.home / 'packages/app-server-daemon/current/bin/codex'
        runtime.parent.mkdir(parents=True)
        runtime.write_text('#!/bin/sh\n')
        runtime.chmod(0o700)
        with patch('bridge.clients.codex.cli_session.shutil.which', return_value=None), \
                patch('bridge.clients.codex.cli_session.Path.home', return_value=self.home):
            self.assertEqual(_local_cli(self.home, 'codex'), str(runtime))

    def test_missing_daemon_package_does_not_start_or_install(self):
        self.socket = Path(self.fake.socket_path)
        self.socket.unlink()
        with self.assertRaises(AppServerUnavailable):
            self.sessions.attach('thread-a', explicit=True)
        self.assertEqual(self.fake.sent, [])
        self.assertIsNone(_managed_cli(self.home))

    def test_remote_start_uses_saved_login_shell_and_checks_package(self):
        command = _ssh_daemon_start_command('saved-host')
        self.assertEqual(command[-2], 'saved-host')
        inner = shlex.split(command[-1])[3]
        self.assertIn('CODEX_HOME', inner)
        self.assertIn('packages/app-server-daemon', inner)
        self.assertIn('app-server daemon start', inner)
        self.assertNotIn('daemon bootstrap', inner)

    def test_explicit_start_uses_selected_home_without_installing(self):
        managed = self.home / 'packages/app-server-daemon/current/bin/codex'
        managed.parent.mkdir(parents=True)
        managed.write_text('#!/bin/sh\n')
        managed.chmod(0o700)
        self.assertTrue(self.sessions.available())
        report = {'status': 'started', 'socketPath': self.fake.socket_path}
        with patch('bridge.clients.codex.cli_session.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, json.dumps(report))) as run:
            self.sessions._start_daemon()
        self.assertEqual(run.call_args.args[0], [str(managed), 'app-server', 'daemon', 'start'])
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], str(self.home))

    def test_same_thread_explicit_attaches_share_one_resume(self):
        entered, release = threading.Event(), threading.Event()
        original = self.fake.request
        def request(method, params, **kwargs):
            if method == 'thread/resume':
                entered.set()
                release.wait(2)
            return original(method, params, **kwargs)
        self.fake.request = request
        results = []
        first = threading.Thread(target=lambda: results.append(self.sessions.attach('thread-a', explicit=True)))
        second = threading.Thread(target=lambda: results.append(self.sessions.attach('thread-a', explicit=True)))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(len(results), 2)
        self.assertEqual(len([method for method, _ in self.fake.sent if method == 'thread/resume']), 1)


if __name__ == '__main__':
    unittest.main()
