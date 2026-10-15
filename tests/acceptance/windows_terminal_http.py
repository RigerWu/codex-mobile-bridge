#!/usr/bin/env python3
"""Exercise an installed Windows gateway's terminal HTTP route in isolation.

Usage: python tests/acceptance/windows_terminal_http.py --gateway ABSOLUTE_EXE --test-root ABSOLUTE_DOT_TMP_DIR
The script creates a synthetic Codex history row; it never starts a model CLI.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import uuid


def require(value, message):
    if not value:
        raise AssertionError(message)


class HTTP:
    def __init__(self, port):
        self.port = port
        self.cookie = None
        self.csrf = None
        self.trace = []

    def request(self, method, path, body=None, csrf=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = {'Origin': f'http://127.0.0.1:{self.port}', 'Content-Type': 'application/json'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if method == 'POST' and csrf and self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            reply = connection.getresponse()
            raw = reply.read()
            status = reply.status
            cookie = reply.getheader('Set-Cookie')
        finally:
            connection.close()
        self.trace.append({'method': method, 'route': path.split('?', 1)[0], 'status': status})
        return status, json.loads(raw), cookie

    def checked(self, method, path, body=None):
        status, value, _ = self.request(method, path, body)
        require(status == 200, f'{method} {path} returned {status}: {value.get("error", "")}')
        return value

    def login(self, password):
        status, value, cookie = self.request('POST', '/api/login',
                                              {'username': 'admin', 'password': password})
        require(status == 200 and cookie and value.get('csrf'), 'Installed gateway login failed')
        self.cookie = cookie.split(';', 1)[0]
        self.csrf = value['csrf']


def until(check, description, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = check()
        except OSError:
            value = None
        if value:
            return value
        time.sleep(.1)
    raise AssertionError(description + ' timed out')


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def password_record(password):
    salt = secrets.token_bytes(16)
    return {'algorithm': 'pbkdf2-sha256', 'iterations': 600000, 'salt': salt.hex(),
            'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', type=Path, required=True)
    parser.add_argument('--test-root', type=Path, required=True)
    args = parser.parse_args()
    require(sys.platform == 'win32', 'This acceptance requires Windows ConPTY')
    gateway = args.gateway.resolve(strict=True)
    require(gateway.is_file() and gateway.name.lower().endswith('.exe'), 'Supply an installed gateway exe')
    test_root = args.test_root.absolute()
    require('.tmp' in test_root.parts and not test_root.is_symlink(), 'Use an isolated project .tmp directory')
    run = test_root / ('terminal-http-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    data, home, workspace = (run / name for name in ('data', 'codex-home', 'workspace'))
    for path in (data, home, workspace, workspace / 'nested'):
        path.mkdir()
    thread_id, tab_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(home / 'state_fixture.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, originator TEXT, '
                   'updated_at INTEGER, archived INTEGER)')
        db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)',
                   (thread_id, str(workspace), 'Terminal acceptance', 'Codex Desktop', int(time.time()), 0))
    password = secrets.token_urlsafe(18)
    port = free_port()
    (data / 'config.json').write_text(json.dumps({'auth': {'mode': 'password', 'username': 'admin',
        **password_record(password)}, 'origins': [], 'connections': []}), encoding='utf-8')
    (data / 'desktop.json').write_text(json.dumps({'port': port, 'lan': False, 'tunnel': False,
        'autoStart': False, 'connections': [], 'codexHome': str(home), 'ipcPath': '', 'codexBin': ''}), encoding='utf-8')
    env = {**os.environ, 'CODEX_HOME': str(home), 'CMB_DATA_DIR': str(data)}
    log = (run / 'gateway.log').open('wb')
    process = subprocess.Popen([str(gateway), 'serve', '--data-dir', str(data)],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)
    http = HTTP(port)
    path = f'/api/sessions/{thread_id}/terminal'
    summary = {'schema': 1, 'status': 'failed', 'gateway': str(gateway), 'run': str(run),
               'threadId': thread_id, 'checks': {}, 'error': None}
    cursor = 0
    output = ''

    def poll():
        nonlocal cursor, output
        value = http.checked('GET', f'{path}?mode=pty&id={tab_id}&after={cursor}')
        output += value['output']
        cursor = value['cursor']
        return value

    def input_text(value):
        http.checked('POST', path, {'action': 'input', 'id': tab_id, 'inputId': str(uuid.uuid4()), 'data': value})

    def streamed(marker):
        poll()
        return marker(output)

    try:
        until(lambda: http.request('GET', '/api/auth')[0] == 200, 'Installed HTTP startup', 40)
        require(http.request('GET', path)[0] == 401, 'Terminal GET must require login')
        http.login(password)
        require(http.request('POST', path, {'action': 'open', 'id': tab_id, 'cols': 80, 'rows': 24}, csrf=False)[0] == 403,
                'Terminal open must require CSRF')
        info = http.checked('GET', path)
        require(Path(info['cwd']) == workspace, 'Synthetic chat cwd was not resolved')
        opened = http.checked('POST', path, {'action': 'open', 'id': tab_id, 'cols': 80, 'rows': 24})
        require(opened['running'], 'ConPTY did not start')
        until(lambda: streamed(lambda text: '>' in text), 'Initial cmd prompt')
        summary['checks']['authCsrfOpen'] = True

        input_text('cd nested\rset CMB_HTTP_STATE=retained\r')
        reopened = http.checked('POST', path, {'action': 'open', 'id': tab_id, 'cols': 80, 'rows': 24})
        require(reopened['running'], 'Opening the same tab restarted or closed its shell')
        require(http.checked('POST', path, {'action': 'resize', 'id': tab_id, 'cols': 100, 'rows': 30})['resized'],
                'Resize failed')
        input_text('echo %CMB_HTTP_STATE%> continuity.txt\r')
        continuity = workspace / 'nested' / 'continuity.txt'
        until(lambda: continuity.exists(), 'cd/env persistence')
        require(continuity.read_text().strip() == 'retained', 'Reopened tab lost cwd or environment')
        summary['checks']['reopenResizeCwdEnv'] = True

        cursor = poll()['cursor']
        output = ''
        input_text('ping 127.0.0.1 -n 2\r')
        until(lambda: streamed(lambda text: text.count('127.0.0.1') >= 3), 'Ping output', 20)
        input_text('echo done> ping-done.txt\r')
        until(lambda: (workspace / 'nested' / 'ping-done.txt').exists(), 'Ping command completion')
        summary['checks']['pingStream'] = True

        cursor = poll()['cursor']
        output = ''
        input_text('ping 127.0.0.1 -t\r')
        until(lambda: streamed(lambda text: text.count('127.0.0.1') >= 3), 'Continuous ping output', 20)
        cursor = poll()['cursor']
        output = ''
        input_text('\x03')
        until(lambda: streamed(lambda text: '>' in text), 'Prompt after Ctrl+C')
        input_text('echo survived> after-ctrl-c.txt\r')
        after = workspace / 'nested' / 'after-ctrl-c.txt'
        until(lambda: after.exists(), 'Shell after Ctrl+C')
        require(after.read_text().strip() == 'survived', 'Ctrl+C killed the shell')
        summary['checks']['ctrlCShellAlive'] = True

        http.checked('POST', path, {'action': 'close', 'id': tab_id})
        until(lambda: not poll()['running'], 'Terminal close', 20)
        summary['checks']['close'] = True
        summary['status'] = 'passed'
    except Exception as exc:
        summary['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        (run / 'http-trace.json').write_text(json.dumps(http.trace, indent=2), encoding='utf-8')
        (run / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        try:
            subprocess.run([str(gateway), 'stop', '--data-dir', str(data)], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        log.close()
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
