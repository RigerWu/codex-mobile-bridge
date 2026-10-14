#!/usr/bin/env python3
"""Isolated Codex CLI account transaction against two loopback Responses APIs."""

import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bridge.features.accounts.accounts import Accounts, ManagedRPC
from bridge.features.accounts.cli_sync import LocalCliSync


class MockAPI(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_):
        pass

    def do_CONNECT(self):
        self.calls.append(('external', self.path, None, None))
        self.send_error(403)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self.calls.append(('model', self.path, self.headers.get('Authorization'), body.get('model')))
        response = {'id': 'resp_fixture', 'object': 'response', 'status': 'completed', 'output': [],
                    'usage': {'input_tokens': 1, 'output_tokens': 0, 'total_tokens': 1}}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.wfile.write(('data: ' + json.dumps({'type': 'response.completed', 'response': response}) + '\n\n').encode())


class TurnRPC(ManagedRPC):
    METHODS = ManagedRPC.METHODS | {'thread/start', 'turn/start'}

    def request(self, method, params=None):
        if method == 'initialize':
            params = {**params, 'clientInfo': {**params['clientInfo'], 'name': 'codex-tui'}}
        return super().request(method, params)


def native_turn(home, executable, project, model):
    with TurnRPC(home, executable) as rpc:
        config = rpc.request('config/read', {'includeLayers': False})['config']
        assert config['model'] == model and config['model_provider'] == 'bridge_api'
        thread = rpc.request('thread/start', {'cwd': str(project), 'modelProvider': 'bridge_api',
                                               'model': model, 'approvalPolicy': 'never',
                                               'threadSource': 'cli'})['thread']
        rpc.request('turn/start', {'threadId': thread['id'], 'input': [
            {'type': 'text', 'text': 'Synthetic account routing fixture only.'}]})
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                event = rpc.messages.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty:
                break
            if event and event.get('method') == 'turn/completed':
                assert event['params']['turn']['status'] == 'completed'
                return
        raise AssertionError('Native CLI turn did not complete')


def main():
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('Installed Codex CLI unavailable')
    temp_parent = ROOT/'.tmp'
    temp_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cli-sync-native-', dir=temp_parent) as temp:
        root = Path(temp)
        previous_cwd = Path.cwd()
        os.chdir(root)
        home = root/'codex-home'
        project = root/'project'
        home.mkdir()
        project.mkdir()
        (home/'config.toml').write_text('cli_auth_credentials_store="file"\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), MockAPI)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        proxy = {'HTTP_PROXY': origin, 'HTTPS_PROXY': origin, 'ALL_PROXY': origin,
                 'http_proxy': origin, 'https_proxy': origin, 'all_proxy': origin,
                 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE',
                    'CODEX_THREAD_ID', 'CODEX_SESSION_ID'):
            proxy[key] = ''
        cli = SimpleNamespace(lock=threading.RLock(), joined={}, attaching={}, requests={},
                              client=SimpleNamespace(socket_path='cli.sock'))
        bridge = SimpleNamespace(codex_home=home, data_dir=root, catalog_reader=SimpleNamespace(
            executable=Path(executable)), remote_bridges={}, live={}, submissions={},
            lock=threading.RLock(), cli_sessions=cli)
        try:
            with patch.dict(os.environ, proxy), patch(
                    'bridge.features.accounts.cli_sync.DesktopApp.discover', return_value=None), patch(
                    'bridge.features.accounts.accounts.DesktopApp.discover', return_value=None):
                bridge.accounts = Accounts(bridge)
                sync = LocalCliSync(bridge)
                assert sync.preflight('codex') == {'sharedWithDesktop': True,
                    'desktopRunning': False, 'needsDesktopSwitch': False}
                for label in ('A', 'B'):
                    bridge.accounts.add_api({'name': label, 'baseUrl': origin+f'/{label.lower()}/v1',
                        'apiKey': f'fixture-key-{label}', 'model': f'fixture-model-{label}'})
                a, b = bridge.accounts.index['accounts']
                assert sync.apply('codex', a)['status'] == 'applied'
                assert sync.verify('codex', a)['status'] == 'verified'
                before = sync.snapshot('codex')
                with patch.object(sync, 'verify', side_effect=ValueError('fixture failure')):
                    try:
                        sync.apply('codex', b)
                    except ValueError as error:
                        assert str(error) == 'fixture failure'
                    else:
                        raise AssertionError('Expected injected switch failure')
                assert sync.snapshot('codex') == before
                assert sync.verify('codex', a)['status'] == 'verified'
                cli.joined['fixture-thread'] = object()
                try:
                    sync.preflight('codex')
                except ValueError:
                    pass
                else:
                    raise AssertionError('Joined CLI session did not block switching')
                cli.joined.clear()
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(cli.client.socket_path)
                    listener.listen(1)
                    try:
                        sync.preflight('codex')
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('Live CLI daemon did not block switching')
                Path(cli.client.socket_path).unlink()
                assert sync.apply('codex', b)['status'] == 'applied'
                assert sync.verify('codex', b)['status'] == 'verified'
                native_turn(home, executable, project, b['model'])
                expected = ('model', '/b/v1/responses', 'Bearer fixture-key-B', 'fixture-model-B')
                assert expected in MockAPI.calls, [(kind, path, model) for kind, path, _, model in MockAPI.calls]
                blocked = sum(kind == 'external' for kind, *_ in MockAPI.calls)
                print('PASS: stopped Desktop; A to B transaction; injected rollback; joined and daemon blockers; native B endpoint, credential and model; '
                      f'{blocked} external CONNECT attempts denied')
        finally:
            server.shutdown()
            server.server_close()
            os.chdir(previous_cwd)


if __name__ == '__main__':
    main()
