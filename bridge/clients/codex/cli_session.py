"""Explicitly join threads already loaded in the existing CLI app-server."""

import copy
import os
import shlex
import shutil
import threading
from pathlib import Path

from bridge.clients.codex.app_server import (
    AppServerError, AppServerUnavailable, AppServerUnsupported, CodexAppServer)
from bridge.clients.codex.catalog import Catalog


def _ssh_proxy_command(alias):
    if not alias or alias.startswith('-') or any(c.isspace() for c in alias):
        raise ValueError('Invalid SSH host alias')
    # Match RemoteStore's explicit user login shell, so it resolves the same
    # CODEX_HOME and PATH. Quote the inner program as one -lc argument.
    inner = ('if command -v codex >/dev/null 2>&1; then '
             'exec codex app-server proxy; '
             'elif test -x "$HOME/.local/bin/codex"; then '
             'exec "$HOME/.local/bin/codex" app-server proxy; else exit 127; fi')
    remote = 'exec "$SHELL" -lc ' + shlex.quote(inner)
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UpdateHostKeys=no', alias, remote]


def _local_cli(home, configured):
    if configured and configured != 'codex':
        path = Path(configured)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise AppServerUnavailable('Configured Codex CLI is unavailable')
    candidates = (shutil.which('codex'), Path.home()/'.local/bin/codex',
                  Path(home)/'packages/app-server-daemon/current/bin/codex')
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidate = Catalog.find_runtime()
    if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise AppServerUnavailable('Codex CLI executable is unavailable')


class CliSessions:
    def __init__(self, home, on_state, on_disconnect, cli='codex', alias=None,
                 process_factory=None):
        self.home = Path(home)
        self.alias = alias
        self.remote_home = None
        command = _ssh_proxy_command(alias) if alias else None
        self.on_state = on_state
        self.on_disconnect = on_disconnect
        self.lock = threading.RLock()
        self.joined = {}
        self.attaching = set()
        self.metadata = {}
        self.states = {}
        self.requests = {}
        self.consumed_requests = {}
        self.refreshing = set()
        self.refresh_pending = set()
        self.client = CodexAppServer(
            self.home / 'app-server-control/app-server-control.sock', cli=cli,
            on_event=self._event, on_server_request=self._server_request,
            on_disconnect=self._disconnect, process_factory=process_factory,
            command=command, check_socket=alias is None)
        self.configured_cli = cli

    def available(self):
        return self.alias is not None or Path(self.client.socket_path).exists()

    def attach(self, thread_id, explicit=False):
        """Passive inspection never resumes a thread; explicit joins require loaded state."""
        if not explicit:
            return None
        if not self.available():
            raise AppServerUnavailable('Existing Codex CLI runtime is unavailable')
        if self.alias is None and isinstance(self.client, CodexAppServer):
            executable = _local_cli(self.home, self.configured_cli)
            self.client.command[0] = executable
        info = self.client.connect()
        if self.alias is None and Path(info['codexHome']) != self.home:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        if self.alias is not None:
            remote_home = Path(info['codexHome'])
            if not remote_home.is_absolute() or (self.remote_home is not None and
                    remote_home != self.remote_home):
                raise AppServerUnavailable('Remote Codex CLI runtime identity changed')
            self.remote_home = remote_home
        with self.lock:
            if thread_id in self.joined:
                return copy.deepcopy(self.states[thread_id])
        if not self.client.is_loaded(thread_id):
            raise AppServerUnavailable('CLI thread is not loaded in the existing runtime')
        before = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False},
                                     replay_safe=True)['thread']
        if before.get('id') != thread_id or before.get('status', {}).get('type') == 'notLoaded':
            raise AppServerUnavailable('CLI thread is no longer loaded')
        # Resume rejoins a running thread, but this protocol has no atomic
        # loaded-only guard. A thread unloading after the checks above can be
        # cold-restored by resume. Only an explicit user connection reaches here;
        # callers requiring strict existing-owner proof must not use this path.
        with self.lock:
            self.attaching.add(thread_id)
        try:
            resumed = self.client.request('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
        except Exception:
            with self.lock:
                self.attaching.discard(thread_id)
                self.requests.pop(thread_id, None)
            raise
        after = resumed.get('thread') or {}
        if (after.get('id') != thread_id or after.get('cwd') != before.get('cwd') or
                resumed.get('modelProvider') != before.get('modelProvider')):
            with self.lock:
                self.attaching.discard(thread_id)
                self.requests.pop(thread_id, None)
            raise AppServerUnsupported('CLI thread identity changed during join')
        with self.lock:
            self.joined[thread_id] = self.client.process
            self.attaching.discard(thread_id)
            self.metadata[thread_id] = {'model': resumed.get('model'),
                                        'modelProvider': resumed.get('modelProvider'),
                                        'cwd': resumed.get('cwd')}
        return self.refresh(thread_id)

    def refresh(self, thread_id):
        self._require_joined(thread_id)
        result = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': True},
                                     replay_safe=True)
        thread = result.get('thread') or {}
        if thread.get('id') != thread_id:
            raise AppServerUnsupported('CLI thread read returned a different thread')
        if (thread.get('status') or {}).get('type') == 'notLoaded':
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread unloaded')
        with self.lock:
            metadata = self.metadata.get(thread_id)
        if metadata is None:
            raise AppServerUnavailable('CLI thread is no longer joined')
        if (thread.get('cwd') != metadata['cwd'] or
                thread.get('modelProvider') != metadata['modelProvider']):
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread provider or working directory changed')
        with self.lock:
            pending = list(self.requests.get(thread_id, {}).values())
            state = self._state(thread, pending, metadata)
            self.states[thread_id] = state
        self.on_state(thread_id, copy.deepcopy(state))
        return copy.deepcopy(state)

    def call(self, thread_id, desktop_method, params, timeout=30):
        self._require_joined(thread_id)
        if not self.client.is_loaded(thread_id):
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread unloaded before the operation')
        self._check_identity(thread_id)
        if desktop_method == 'thread-follower-start-turn':
            start = params.get('turnStart') or {}
            request = start.get('request') or {}
            context = start.get('context') or {}
            inputs = self._inputs(request.get('input', []), context)
            if request.get('collaborationMode'):
                raise AppServerUnsupported('CLI collaboration mode override is unavailable')
            result = self.client.request('turn/start', {
                'threadId': thread_id, 'input': inputs,
                'clientUserMessageId': request.get('clientUserMessageId')}, timeout=timeout)
            self._queue_refresh(thread_id)
            return result
        if desktop_method == 'thread-follower-steer-turn':
            if params.get('attachments') or (params.get('restoreMessage') or {}).get('context', {}).get('fileAttachments'):
                raise AppServerUnsupported('CLI steering with files is unavailable')
            with self.lock:
                active = next((t for t in reversed(self.states[thread_id]['turns'])
                               if t.get('status') == 'inProgress'), None)
            if not active:
                raise AppServerUnavailable('No active CLI turn to steer')
            result = self.client.request('turn/steer', {
                'threadId': thread_id, 'expectedTurnId': active['turnId'],
                'input': self._inputs(params.get('input', []), {}),
                'clientUserMessageId': params.get('clientUserMessageId')}, timeout=timeout)
            self._queue_refresh(thread_id)
            return result
        if desktop_method == 'thread-follower-interrupt-turn':
            turn_id = params.get('expectedTurnId')
            if not turn_id:
                raise AppServerUnsupported('Missing active turn id')
            return self.client.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=timeout)
        approval_methods = {
            'thread-follower-command-approval-decision': 'item/commandExecution/requestApproval',
            'thread-follower-file-approval-decision': 'item/fileChange/requestApproval',
            'thread-follower-permissions-request-approval-response': 'item/permissions/requestApproval',
            'thread-follower-submit-user-input': 'item/tool/requestUserInput',
            'thread-follower-submit-mcp-server-elicitation-response': 'mcpServer/elicitation/request',
        }
        if desktop_method in approval_methods:
            request_id = params.get('requestId')
            expected = approval_methods[desktop_method]
            if desktop_method in ('thread-follower-command-approval-decision',
                                  'thread-follower-file-approval-decision'):
                if 'decision' not in params:
                    raise AppServerUnsupported('Missing CLI approval decision')
                result = {'decision': params['decision']}
            else:
                if 'response' not in params:
                    raise AppServerUnsupported('Missing CLI approval response')
                result = params['response']
            with self.lock:
                pending = self.requests.get(thread_id, {}).get(request_id)
                if not pending or (pending['method'] != expected and not (
                        expected == 'item/tool/requestUserInput' and pending['method'] == 'tool/requestUserInput')):
                    raise AppServerUnavailable('CLI approval is no longer pending')
                # Claim the native request id before writing. A send timeout or
                # disconnect has an unknown result and must not permit replay.
                self.requests[thread_id].pop(request_id)
                self.consumed_requests.setdefault(thread_id, set()).add(request_id)
                state = self.states.get(thread_id)
                if state is not None:
                    state['requests'] = list(self.requests[thread_id].values())
                    snapshot = copy.deepcopy(state)
                else:
                    snapshot = None
            if snapshot is not None:
                self.on_state(thread_id, snapshot)
            self.client.respond(request_id, result)
            self._queue_refresh(thread_id)
            return {'submitted': True}
        raise AppServerUnsupported('CLI operation is not supported by this adapter')

    @staticmethod
    def _inputs(inputs, context):
        result = []
        for value in inputs:
            kind = value.get('type')
            if kind in ('text', 'localImage', 'skill'):
                result.append({key: value[key] for key in ('type', 'text', 'text_elements', 'path', 'name') if key in value})
            else:
                raise AppServerUnsupported('Unsupported CLI input type')
        files = context.get('fileAttachments') or []
        if files:
            for item in files:
                path = item.get('path')
                if not path or not Path(path).is_absolute():
                    raise AppServerUnsupported('Invalid CLI file attachment path')
            prefix = '# Files mentioned by the user:\n' + '\n'.join(
                '- ' + item['path'] for item in files) + '\n\n## My request:\n'
            first_text = next((item for item in result if item['type'] == 'text'), None)
            if first_text is None:
                result.insert(0, {'type': 'text', 'text': prefix})
            else:
                first_text['text'] = prefix + first_text.get('text', '')
        return result

    @staticmethod
    def _state(thread, requests, metadata):
        status = thread.get('status') or {'type': 'idle'}
        turns = []
        for turn in thread.get('turns') or []:
            turns.append({'turnId': turn.get('id'), 'status': turn.get('status'),
                          'turnStartedAtMs': (turn.get('startedAt') or 0) * 1000,
                          'items': turn.get('items') or [], 'error': turn.get('error')})
        return {'id': thread['id'], 'title': thread.get('name') or thread.get('preview') or '未命名聊天',
                'cwd': thread.get('cwd') or metadata['cwd'],
                'modelProvider': thread.get('modelProvider') or metadata['modelProvider'],
                'latestModel': thread.get('model') or metadata['model'],
                'latestReasoningEffort': thread.get('reasoningEffort'),
                'threadRuntimeStatus': status, 'turns': turns, 'requests': requests}

    def _require_joined(self, thread_id):
        with self.lock:
            if self.joined.get(thread_id) is not self.client.process or self.client.process is None:
                raise AppServerUnavailable('CLI thread is not joined on this connection')

    def _check_identity(self, thread_id):
        result = self.client.request('thread/read', {'threadId': thread_id,
                                                      'includeTurns': False}, replay_safe=True)
        thread = result.get('thread') or {}
        with self.lock:
            metadata = self.metadata.get(thread_id)
        if metadata is None:
            raise AppServerUnavailable('CLI thread is no longer joined')
        if (thread.get('id') != thread_id or thread.get('status', {}).get('type') == 'notLoaded' or
                thread.get('cwd') != metadata['cwd'] or
                thread.get('modelProvider') != metadata['modelProvider']):
            self._invalidate(thread_id)
            raise AppServerUnavailable('CLI thread identity changed before the operation')

    def _event(self, event):
        method = event.get('method')
        params = event.get('params') or {}
        thread_id = params.get('threadId')
        with self.lock:
            joined = thread_id in self.joined
        if not joined:
            return
        if method == 'thread/closed' or (method == 'thread/status/changed' and
                (params.get('status') or {}).get('type') == 'notLoaded'):
            self._invalidate(thread_id)
            return
        with self.lock:
            state = self.states.get(thread_id)
            if state is None:
                self._queue_refresh(thread_id)
                return
            if method == 'serverRequest/resolved':
                self.requests.get(thread_id, {}).pop(params.get('requestId'), None)
                state['requests'] = list(self.requests.get(thread_id, {}).values())
            elif method == 'thread/status/changed':
                state['threadRuntimeStatus'] = params.get('status') or state['threadRuntimeStatus']
            elif method in ('turn/started', 'turn/completed'):
                turn = params.get('turn') or {}
                row = next((item for item in state['turns'] if item['turnId'] == turn.get('id')), None)
                if row is None:
                    row = {'turnId': turn.get('id'), 'items': []}
                    state['turns'].append(row)
                row.update(status=turn.get('status'), error=turn.get('error'))
                state['threadRuntimeStatus'] = {'type': 'active' if method == 'turn/started' else 'idle'}
                if turn.get('items'):
                    row['items'] = turn['items']
                if method == 'turn/completed':
                    pending = self.requests.get(thread_id, {})
                    for request_id in list(pending):
                        if (pending[request_id].get('params') or {}).get('turnId') == turn.get('id'):
                            pending.pop(request_id)
                    state['requests'] = list(pending.values())
            elif method in ('item/started', 'item/completed'):
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn and isinstance(params.get('item'), dict):
                    item = params['item']
                    items = turn['items']
                    for index, existing in enumerate(items):
                        if existing.get('id') == item.get('id'):
                            items[index] = item
                            break
                    else:
                        items.append(item)
            elif method == 'item/agentMessage/delta':
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn:
                    item = next((item for item in turn['items'] if item.get('id') == params.get('itemId')), None)
                    if item is None:
                        item = {'id': params.get('itemId'), 'type': 'agentMessage', 'text': ''}
                        turn['items'].append(item)
                    if isinstance(params.get('delta'), str):
                        item['text'] = item.get('text', '') + params['delta']
            elif method == 'item/commandExecution/outputDelta':
                turn = next((item for item in state['turns'] if item['turnId'] == params.get('turnId')), None)
                if turn:
                    item = next((item for item in turn['items'] if item.get('id') == params.get('itemId')), None)
                    if item is not None and isinstance(params.get('delta'), str):
                        item['aggregatedOutput'] = item.get('aggregatedOutput', '') + params['delta']
            else:
                return
            snapshot = copy.deepcopy(state)
        self.on_state(thread_id, snapshot)

    def _server_request(self, request):
        thread_id = (request.get('params') or {}).get('threadId')
        with self.lock:
            if thread_id not in self.joined and thread_id not in self.attaching:
                return
            if request['id'] in self.consumed_requests.get(thread_id, set()):
                return
            self.requests.setdefault(thread_id, {})[request['id']] = request
            state = self.states.get(thread_id)
            if state is not None:
                state['requests'] = list(self.requests[thread_id].values())
                snapshot = copy.deepcopy(state)
            else:
                snapshot = None
        if snapshot is not None:
            self.on_state(thread_id, snapshot)

    def _queue_refresh(self, thread_id):
        with self.lock:
            if thread_id in self.refreshing:
                self.refresh_pending.add(thread_id)
                return
            self.refreshing.add(thread_id)
        def run():
            try:
                while True:
                    self.refresh(thread_id)
                    with self.lock:
                        if thread_id not in self.refresh_pending:
                            break
                        self.refresh_pending.discard(thread_id)
            except AppServerError:
                self._invalidate(thread_id)
            finally:
                with self.lock:
                    self.refreshing.discard(thread_id)
                    self.refresh_pending.discard(thread_id)
        threading.Thread(target=run, daemon=True).start()

    def _disconnect(self):
        with self.lock:
            self.joined.clear()
            self.attaching.clear()
            self.metadata.clear()
            self.states.clear()
            self.requests.clear()
            self.consumed_requests.clear()
            self.refresh_pending.clear()
        self.on_disconnect()

    def _invalidate(self, thread_id):
        with self.lock:
            self.joined.pop(thread_id, None)
            self.attaching.discard(thread_id)
            self.metadata.pop(thread_id, None)
            self.states.pop(thread_id, None)
            self.requests.pop(thread_id, None)
            self.consumed_requests.pop(thread_id, None)
        self.on_disconnect()

    def close(self):
        self.client.close()
        self._disconnect()
