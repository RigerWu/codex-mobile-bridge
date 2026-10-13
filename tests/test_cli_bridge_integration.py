"""CLI access at the bridge and authenticated HTTP boundaries, using only fixtures."""
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.api.httpd import GatewayServer
from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnknownResult
from bridge.clients.codex.cli_session import CliSessions
from bridge.clients.codex.ipc import IPCError
from bridge.features.auth.auth import password_record


ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.tmp').mkdir(exist_ok=True)


class CliBridgeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.home = Path(self.temp.name)
        self.thread_id = str(uuid.uuid4())
        self.private_text = 'Private CLI conversation text'
        rollout = self.home / 'sessions' / 'rollout.jsonl'
        rollout.parent.mkdir()
        rollout.write_text(json.dumps({'type': 'response_item', 'payload': {'type': 'message',
            'role': 'assistant', 'content': [{'type': 'text', 'text': self.private_text}]}}) + '\n')
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, '
                       'originator TEXT, source TEXT, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',
                       (self.thread_id, 'Private CLI title', '/private/user/project', 1, 0,
                        'codex_cli_rs', 'cli', str(rollout)))
        self.native_state = {'id': self.thread_id, 'title': 'CLI fixture', 'cwd': '/private/user/project',
                             'modelProvider': 'openai', 'latestModel': 'test-model',
                             'threadRuntimeStatus': {'type': 'idle'}, 'turns': [], 'requests': []}
        self.cli = Mock()
        self.cli.available.return_value = True
        self.cli.attach.return_value = self.native_state
        self.cli.call.return_value = {'ok': True}
        self.bridge = Bridge(self.home, self.home / 'bridge-data', codex_bin=self.home / 'no-codex',
                             cli_sessions=self.cli)
        self.bridge.ipc.connect = Mock(side_effect=IPCError('desktop absent'))
        self.bridge.ipc.follow = Mock(side_effect=AssertionError('desktop follow'))

    def tearDown(self):
        deadline = time.monotonic() + 2
        while any(session.connecting for session in list(self.bridge.live.values())) and time.monotonic() < deadline:
            time.sleep(.01)
        self.bridge.close()
        self.temp.cleanup()

    def wait_for_background_read(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            session = self.bridge.live.get(self.thread_id)
            if session and not session.connecting and session.state is not None:
                return
            time.sleep(.01)
        self.fail('passive history read did not settle')


class CliBridgeTests(CliBridgeFixture):
    def test_missing_native_runtime_explains_how_to_enable_continuation(self):
        self.cli.available.return_value = False
        view = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(view['access']['canReconnect'])
        self.assertIn('Codex CLI', view['access']['reason'])
        self.assertIn('安装或更新', view['access']['reason'])
        self.cli.attach.assert_not_called()

    def test_passive_view_reads_saved_history_without_native_join_or_navigation(self):
        with patch('bridge.app.service.open_in_desktop') as navigation:
            view = self.bridge.view(self.thread_id, background=True)
            self.wait_for_background_read()
        self.assertFalse(view['connected'])
        self.assertEqual(view['access']['source'], 'cli')
        self.assertFalse(view['access']['canSend'])
        self.assertIn(self.private_text, str(self.bridge.view(self.thread_id, attach=False)))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()
        navigation.assert_not_called()

    def test_full_history_uses_saved_rollout_without_attaching_either_runtime(self):
        with patch.object(self.bridge.store, 'history', wraps=self.bridge.store.history) as history:
            result = self.bridge.full_history(self.thread_id)
        self.assertEqual(result, {'loaded': True})
        self.assertTrue(any(call.kwargs.get('turn_limit') is None for call in history.call_args_list))
        self.assertIn(self.private_text, str(self.bridge.view(self.thread_id, attach=False)))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()
        self.bridge.ipc.follow.assert_not_called()

    def test_desktop_snapshot_with_same_id_cannot_take_over_cli(self):
        session = self.bridge.activate(self.thread_id)
        original = session.state
        desktop = {**self.native_state, 'title': 'Desktop copy', 'modelProvider': 'other-provider'}
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
                            'sourceClientId': 'desktop-owner', 'params': {
                                'hostId': 'local', 'conversationId': self.thread_id,
                                'change': {'type': 'snapshot', 'revision': 1, 'conversationState': desktop}}})
        self.assertIs(session.state, original)
        self.assertEqual(session.owner, 'cli:' + self.thread_id)
        self.assertEqual(session.runtime, 'cli')
        self.assertTrue(session.connected)

    def test_bridge_preserves_native_request_ids_for_approval_and_input(self):
        self.bridge.activate(self.thread_id)
        session = self.bridge.live[self.thread_id]
        for request_id, method, response, expected_method, expected_payload in [
            (73, 'item/commandExecution/requestApproval', {'decision': 'decline'},
             'thread-follower-command-approval-decision', {'decision': 'decline'}),
            (74, 'item/tool/requestUserInput', {'answers': {'choice': ['continue']}},
             'thread-follower-submit-user-input',
             {'response': {'answers': {'choice': {'answers': ['continue']}}}}),
        ]:
            with self.subTest(method=method):
                params = {'questions': [{'id': 'choice'}]} if 'UserInput' in method else {'command': 'pwd'}
                with session.condition:
                    session.state['requests'] = [{'id': request_id, 'method': method, 'params': params}]
                self.bridge.respond(self.thread_id, str(request_id), response)
                self.assertEqual(self.cli.call.call_args.args[1], expected_method)
                self.assertEqual(self.cli.call.call_args.args[2],
                                 {'requestId': request_id, **expected_payload})

    def test_explicit_activate_and_send_route_to_cli_then_disconnect_is_scoped(self):
        with patch('bridge.app.service.open_in_desktop') as navigation:
            session = self.bridge.activate(self.thread_id)
            self.assertTrue(session.connected)
            self.assertEqual(session.runtime, 'cli')
            self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
            self.bridge.ipc.request = Mock(side_effect=AssertionError('desktop write'))
            result = self.bridge.send(self.thread_id, 'hello', str(uuid.uuid4()))
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(self.cli.call.call_args.args[1], 'thread-follower-start-turn')
            self.assertEqual(self.cli.call.call_args.args[2]['turnStart']['request']['input'][0]['text'], 'hello')
            self.bridge._disconnected()
            self.assertTrue(session.connected, 'desktop IPC loss must not drop CLI runtime')
            self.bridge._cli_disconnected()
            self.assertFalse(session.connected)
            navigation.assert_not_called()

    def test_unsupported_configuration_is_rejected_before_native_write(self):
        self.bridge.activate(self.thread_id)
        with self.assertRaisesRegex(ValueError, 'CLI'):
            self.bridge.settings(self.thread_id, 'test-model', 'high')
        with self.assertRaisesRegex(ValueError, 'CLI'):
            self.bridge.send(self.thread_id, 'planned', str(uuid.uuid4()), work_mode='plan')
        with self.assertRaisesRegex(ValueError, 'CLI'):
            self.bridge.send(self.thread_id, 'skill', str(uuid.uuid4()), skills=['fixture-skill'])
        self.cli.call.assert_not_called()

    def test_unknown_native_send_is_not_replayed_after_disconnect(self):
        self.bridge.activate(self.thread_id)
        self.cli.call.side_effect = AppServerUnknownResult('write result unknown')
        submission_id = str(uuid.uuid4())
        with self.assertRaises(IPCError):
            self.bridge.send(self.thread_id, 'unknown write', submission_id)
        self.assertEqual(self.bridge.submissions[self.thread_id + ':' + submission_id]['status'], 'unknown')
        self.bridge._cli_disconnected()
        self.assertEqual(self.cli.call.call_count, 1)
        self.assertFalse(self.bridge.live[self.thread_id].connected)

    def test_restored_live_entry_uses_store_origin_before_activation(self):
        # A queued submission reconstructed at startup creates a LiveSession
        # before the first list or view request.
        self.bridge.live[self.thread_id] = LiveSession(self.thread_id)
        session = self.bridge.session(self.thread_id, attach=False)
        self.assertEqual(session.origin, 'cli')
        self.assertFalse(self.bridge.view(self.thread_id, attach=False)['access']['canConfigure'])


class CliDiagnosticsHttpTests(CliBridgeFixture):
    @classmethod
    def setUpClass(cls):
        cls.password = password_record('integration password')

    def setUp(self):
        super().setUp()
        config = {'auth': {'mode': 'password', 'username': 'admin', **self.password}, 'origins': []}
        self.server = GatewayServer(('127.0.0.1', 0), self.bridge, config, ROOT / 'web')
        self.port = self.server.server_address[1]
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self.server.origins.add(self.origin)
        self.server.hosts.add('127.0.0.1:' + str(self.port))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        request_headers = {'Origin': self.origin, 'Content-Type': 'application/json', **(headers or {})}
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers=request_headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        conn.close()
        return result

    def test_diagnostics_requires_login_and_exports_only_aggregate_status(self):
        route = '/api/sessions/diagnostics?thread=' + self.thread_id
        self.assertEqual(self.request('GET', route)[0], 401)
        status, headers, login = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'integration password'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        status, headers, report = self.request('GET', route, headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        local = report['hosts'][0]
        self.assertEqual(local['target']['origin'], 'cli')
        self.assertEqual(local['target']['read'], 'ready')
        self.assertFalse(local['target']['archived'])
        self.assertNotIn(self.private_text, str(report))
        self.assertNotIn('Private CLI title', str(report))
        self.assertNotIn('/private/user', str(report))
        self.assertNotIn(self.thread_id, str(report))
        self.assertEqual(self.request('GET', '/api/sessions/diagnostics?extra=1',
                                      headers={'Cookie': cookie})[0], 400)
        self.assertEqual(self.request('GET', '/api/sessions/diagnostics?thread=not-a-uuid',
                                      headers={'Cookie': cookie})[0], 400)

    def test_reconnect_route_requires_csrf_and_only_explicit_activate_joins_cli(self):
        status, headers, login = self.request('POST', '/api/login',
            {'username': 'admin', 'password': 'integration password'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        route = '/api/sessions/' + self.thread_id
        self.assertEqual(self.request('GET', route, headers={'Cookie': cookie})[0], 200)
        self.wait_for_background_read()
        self.cli.attach.assert_not_called()
        reconnect = route + '/reconnect'
        self.assertEqual(self.request('POST', reconnect, {'activate': True},
                                      headers={'Cookie': cookie})[0], 403)
        auth = {'Cookie': cookie, 'X-CSRF-Token': login['csrf']}
        status, _, result = self.request('POST', reconnect, {'activate': True}, headers=auth)
        self.assertEqual(status, 200)
        self.assertTrue(result['connected'])
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)


class CliApprovalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.thread_id = str(uuid.uuid4())
        self.adapter = CliSessions(Path('/fixture/codex-home'), lambda *_: None, lambda: None)
        self.process = object()
        self.adapter.client.process = self.process
        self.adapter.client.is_loaded = Mock(return_value=True)
        self.adapter.client.request = Mock(return_value={'thread': {
            'id': self.thread_id, 'cwd': '/fixture/work', 'modelProvider': 'openai',
            'status': {'type': 'active'}}})
        self.adapter.client.respond = Mock()
        self.adapter.joined[self.thread_id] = self.process
        self.adapter.metadata[self.thread_id] = {'cwd': '/fixture/work', 'modelProvider': 'openai'}

    def test_approval_and_user_input_reply_to_original_jsonrpc_id(self):
        for request_id, method, desktop_method, payload, expected in [
            (73, 'item/commandExecution/requestApproval', 'thread-follower-command-approval-decision',
             {'decision': 'decline'}, {'decision': 'decline'}),
            (74, 'item/tool/requestUserInput', 'thread-follower-submit-user-input',
             {'response': {'answers': {'choice': {'answers': ['continue']}}}},
             {'answers': {'choice': {'answers': ['continue']}}}),
        ]:
            with self.subTest(method=method), patch.object(self.adapter, '_queue_refresh'):
                self.adapter._server_request({'id': request_id, 'method': method,
                                              'params': {'threadId': self.thread_id}})
                self.assertEqual(self.adapter.call(self.thread_id, desktop_method,
                                                  {'requestId': request_id, **payload}), {'submitted': True})
                self.adapter.client.respond.assert_called_with(request_id, expected)
                self.assertNotIn(request_id, self.adapter.requests[self.thread_id])

    def test_disconnect_after_uncertain_approval_does_not_replay_response(self):
        request_id = 91
        self.adapter._server_request({'id': request_id, 'method': 'item/tool/requestUserInput',
                                      'params': {'threadId': self.thread_id}})
        self.adapter.client.respond.side_effect = AppServerUnknownResult('response may have been sent')
        with self.assertRaises(AppServerUnknownResult):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.assertNotIn(request_id, self.adapter.requests[self.thread_id])
        # Even before the delayed disconnect callback, an unknown response
        # must not remain actionable or be replayed by an explicit repeat.
        with self.assertRaises(AppServerUnavailable):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.adapter._disconnect()
        with self.assertRaises(AppServerUnavailable):
            self.adapter.call(self.thread_id, 'thread-follower-submit-user-input',
                              {'requestId': request_id, 'response': {'answers': {'choice': {'answers': ['yes']}}}})
        self.assertEqual(self.adapter.client.respond.call_count, 1)


if __name__ == '__main__':
    unittest.main()
