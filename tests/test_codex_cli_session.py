import tempfile
import unittest
from pathlib import Path

from bridge.clients.codex.app_server import AppServerUnavailable, AppServerUnsupported
from bridge.clients.codex.cli_session import CliSessions


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
            return {'thread': self.thread.copy(), 'modelProvider': 'original-provider'}
        if method == 'turn/start':
            return {'turn': {'id': 'turn-1'}}
        return {}

    def respond(self, request_id, result):
        self.replies.append((request_id, result))

    def close(self):
        self.process = None


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / 'app-server-control').mkdir()
        (self.home / 'app-server-control/app-server-control.sock').touch()
        self.states = []
        self.sessions = CliSessions(self.home, lambda tid, state: self.states.append((tid, state)), lambda: None)
        self.fake = FakeClient(self.home)
        self.sessions.client = self.fake

    def tearDown(self):
        self.temp.cleanup()

    def test_passive_attach_does_not_connect_or_resume(self):
        self.assertIsNone(self.sessions.attach('thread-a'))
        self.assertEqual(self.fake.sent, [])

    def test_explicit_attach_requires_loaded_runtime_and_preserves_identity(self):
        self.fake.loaded = False
        with self.assertRaises(AppServerUnavailable):
            self.sessions.attach('thread-a', explicit=True)
        self.assertFalse(any(method == 'thread/resume' for method, _ in self.fake.sent))
        self.fake.loaded = True
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


if __name__ == '__main__':
    unittest.main()
