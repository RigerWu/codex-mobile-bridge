import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from bridge.clients.codex.app_server import (
    AppServerUnknownResult, AppServerUnsupported, CodexAppServer)


class FixtureProcess:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        request_read, request_write = os.pipe()
        response_read, response_write = os.pipe()
        self.stdin = os.fdopen(request_write, 'wb', buffering=0)
        self.stdout = os.fdopen(response_read, 'rb', buffering=0)
        self.requests = os.fdopen(request_read, 'rb', buffering=0)
        self.responses = os.fdopen(response_write, 'wb', buffering=0)
        self.seen = []
        self.ended = False
        self.drop = set()
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()

    def send(self, value):
        self.responses.write(json.dumps(value).encode() + b'\n')

    def serve(self):
        for line in self.requests:
            message = json.loads(line)
            self.seen.append(message)
            if 'id' not in message or 'method' not in message:
                continue
            method = message['method']
            if method in self.drop:
                continue
            if method == 'initialize':
                result = {'codexHome': '/fixture/.codex', 'platformFamily': 'unix',
                          'platformOs': 'macos', 'userAgent': 'test'}
            elif method == 'thread/loaded/list':
                cursor = message['params']['cursor']
                result = {'data': ['thread-a'] if cursor is None else ['thread-b'],
                          'nextCursor': 'page-2' if cursor is None else None}
            else:
                result = {'ok': True}
            self.send({'id': message['id'], 'result': result})
        self.ended = True

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.requests.close()
        self.responses.close()
        self.ended = True

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return 0


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket = Path(self.temp.name) / 'existing.sock'
        self.socket.touch()
        self.processes = []
        self.events = []
        self.server_requests = []

        def factory(*args, **kwargs):
            process = FixtureProcess(*args, **kwargs)
            self.processes.append(process)
            return process

        self.client = CodexAppServer(self.socket, on_event=self.events.append,
                                     on_server_request=self.server_requests.append,
                                     process_factory=factory)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_attaches_existing_socket_and_lists_loaded_threads(self):
        self.assertEqual(self.client.loaded_threads(), ['thread-a', 'thread-b'])
        process = self.processes[0]
        self.assertEqual(process.argv[-4:], ['app-server', 'proxy', '--sock', str(self.socket)])
        self.assertEqual([m['method'] for m in process.seen[:3]],
                         ['initialize', 'initialized', 'thread/loaded/list'])
        self.assertEqual(self.client.request('thread/read', {'threadId': 'thread-a'},
                                             replay_safe=True), {'ok': True})

    def test_events_and_server_requests_are_forwarded_without_auto_decision(self):
        self.client.connect()
        process = self.processes[0]
        process.send({'method': 'turn/started', 'params': {'threadId': 'thread-a'}})
        process.send({'id': 71, 'method': 'item/commandExecution/requestApproval',
                      'params': {'threadId': 'thread-a'}})
        # A round trip ensures preceding messages have been dispatched.
        self.client.request('thread/read', {'threadId': 'thread-a'}, replay_safe=True)
        self.assertEqual(self.events[0]['method'], 'turn/started')
        self.assertEqual(self.server_requests[0]['id'], 71)
        self.assertFalse(any(m.get('id') == 71 for m in process.seen))
        self.client.respond(71, {'decision': 'decline'})
        self.client.request('thread/read', {'threadId': 'thread-a'}, replay_safe=True)
        self.assertEqual(next(m['result'] for m in process.seen if m.get('id') == 71),
                         {'decision': 'decline'})

    def test_write_timeout_has_unknown_result_and_is_not_retried(self):
        self.client.connect()
        process = self.processes[0]
        process.drop.add('turn/start')
        with self.assertRaises(AppServerUnknownResult):
            self.client.request('turn/start', {'threadId': 'thread-a'}, timeout=.02)
        self.assertEqual(len([m for m in process.seen if m.get('method') == 'turn/start']), 1)

    def test_missing_socket_never_launches_proxy(self):
        self.socket.unlink()
        with self.assertRaises(Exception):
            self.client.connect()
        self.assertEqual(self.processes, [])


if __name__ == '__main__':
    unittest.main()
