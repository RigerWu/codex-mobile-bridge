#!/usr/bin/env python3
"""Isolated managed Claude API selection against two local mock endpoints."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bridge.features.accounts.accounts import private_json
from bridge.features.accounts.cli_sync import LocalCliSync


class MockAPI(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_):
        pass

    def do_CONNECT(self):
        self.calls.append(('external', self.path, '', ''))
        self.send_error(403)

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self.calls.append(('model', self.path, self.headers.get('x-api-key') or
                           self.headers.get('Authorization'), body.get('model')))
        response = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'Fixture reply.'}], 'model': body.get('model'),
                    'stop_reason': 'end_turn', 'stop_sequence': None,
                    'usage': {'input_tokens': 1, 'output_tokens': 1}}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        events = [
            ('message_start', {'type': 'message_start', 'message': {**response, 'content': [], 'stop_reason': None}}),
            ('content_block_start', {'type': 'content_block_start', 'index': 0,
                                     'content_block': {'type': 'text', 'text': ''}}),
            ('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                     'delta': {'type': 'text_delta', 'text': 'Fixture reply.'}}),
            ('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
            ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                               'usage': {'output_tokens': 1}}),
            ('message_stop', {'type': 'message_stop'}),
        ]
        for event, value in events:
            self.wfile.write(f'event: {event}\ndata: {json.dumps(value)}\n\n'.encode())
        self.wfile.flush()


def main():
    binary = shutil.which('claude')
    if not binary:
        raise RuntimeError('Installed Claude CLI unavailable')
    parent = ROOT/'.tmp'
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='claude-sync-native-', dir=parent) as temporary:
        root = Path(temporary)
        native = root/'original-claude'
        native.mkdir()
        original = {'permissions': {'defaultMode': 'plan'}, 'hooks': {'PreToolUse': []},
                    'env': {'UNRELATED_FIXTURE': 'preserved',
                            'ANTHROPIC_API_KEY': 'native-fixture-key',
                            'ANTHROPIC_MODEL': 'native-fixture-model'}}
        private_json(native/'settings.json', original)
        server = ThreadingHTTPServer(('127.0.0.1', 0), MockAPI)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        env = {'HOME': str(root), 'CLAUDE_CONFIG_DIR': str(native), 'ANTHROPIC_API_KEY': 'ambient-wrong',
               'ANTHROPIC_BASE_URL': 'https://ambient.invalid', 'ANTHROPIC_MODEL': 'ambient-wrong-model',
               'HTTP_PROXY': origin, 'HTTPS_PROXY': origin, 'ALL_PROXY': origin,
               'http_proxy': origin, 'https_proxy': origin, 'all_proxy': origin,
               'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        bridge = SimpleNamespace(data_dir=root)
        sync = LocalCliSync(bridge)
        rows = []
        for name in ('A', 'B'):
            identifier = (name.lower()*32)
            path = root/'cli-access'/'claude'/identifier/'api.json'
            private_json(path, {'key': f'fixture-key-{name}'})
            rows.append({'id': identifier, 'kind': 'api', 'directory': str(native),
                         'secretPath': str(path), 'baseUrl': origin+f'/{name.lower()}',
                         'model': f'fixture-model-{name}',
                         'authScheme': 'bearer' if name == 'B' else 'x-api-key'})
        try:
            with patch.dict(os.environ, env):
                assert sync.preflight('claude')['sharedWithDesktop'] is False
                assert sync.apply('claude', rows[0])['status'] == 'applied'
                assert sync.verify('claude', rows[0])['status'] == 'configured'
                assert json.loads((native/'settings.json').read_text())['hooks'] == original['hooks']
                before = (native/'settings.json').read_bytes()
                assert sync.apply('claude', rows[1])['status'] == 'applied'
                assert sync.verify('claude', rows[1])['status'] == 'configured'
                selected = json.loads((native/'settings.json').read_text())
                assert selected['env']['UNRELATED_FIXTURE'] == 'preserved'
                assert selected['permissions'] == original['permissions']
                assert selected['hooks'] == original['hooks']
                run_env = {key: value for key, value in os.environ.items()
                           if not key.startswith('ANTHROPIC_')}
                result = subprocess.run([binary, '-p', 'Synthetic local fixture only.', '--output-format', 'json',
                                         '--model', rows[1]['model'], '--max-turns', '1', '--no-session-persistence'],
                                        cwd=root, env=run_env, capture_output=True, text=True, timeout=35)
                assert any(kind == 'model' and path.split('?', 1)[0] == '/b/v1/messages'
                           and credential == 'Bearer fixture-key-B' and model == 'fixture-model-B'
                           for kind, path, credential, model in MockAPI.calls), [
                    (kind, path, model) for kind, path, _, model in MockAPI.calls]
                assert result.returncode == 0, 'Native CLI rejected mock response'
                sync.restore('claude', {})
                assert (native/'settings.json').read_bytes() == before
                assert sync.apply('claude', {'kind': 'native', 'directory': str(native)})['status'] == 'selected'
                assert sync.verify('claude', {'kind': 'native', 'directory': str(native)})['status'] == 'configured'
                assert json.loads((native/'settings.json').read_text()) == original
                print('PASS: A to B native Claude settings; ambient variables excluded; B endpoint, bearer credential and model; rollback and native switch-back preserve settings')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
