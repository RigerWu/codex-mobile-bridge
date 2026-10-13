"""Explicitly join threads already loaded in the existing CLI app-server."""

import copy
import threading
from pathlib import Path

from bridge.clients.codex.app_server import (
    AppServerError, AppServerUnavailable, AppServerUnsupported, CodexAppServer)


class CliSessions:
    def __init__(self, home, on_state, on_disconnect, cli='codex', alias=None,
                 process_factory=None):
        self.home = Path(home)
        self.alias = alias
        self.on_state = on_state
        self.on_disconnect = on_disconnect
        self.lock = threading.RLock()
        self.joined = {}
        self.metadata = {}
        self.states = {}
        self.requests = {}
        self.refreshing = set()
        self.refresh_pending = set()
        self.client = CodexAppServer(
            self.home / 'app-server-control/app-server-control.sock', cli=cli,
            on_event=self._event, on_server_request=self._server_request,
            on_disconnect=self._disconnect, process_factory=process_factory)

    def available(self):
        return self.alias is None and Path(self.client.socket_path).exists()

    def attach(self, thread_id, explicit=False):
        """Passive inspection never resumes a thread; explicit joins require loaded state."""
        if not explicit:
            return None
        if not self.available():
            raise AppServerUnavailable('Existing Codex CLI runtime is unavailable')
        info = self.client.connect()
        if Path(info['codexHome']) != self.home:
            raise AppServerUnavailable('Codex CLI runtime belongs to another home')
        with self.lock:
            if thread_id in self.joined:
                return copy.deepcopy(self.states[thread_id])
        if not self.client.is_loaded(thread_id):
            raise AppServerUnavailable('CLI thread is not loaded in the existing runtime')
        before = self.client.request('thread/read', {'threadId': thread_id, 'includeTurns': False},
                                     replay_safe=True)['thread']
        if before.get('id') != thread_id or before.get('status', {}).get('type') == 'notLoaded':
            raise AppServerUnavailable('CLI thread is no longer loaded')
        # Resume rejoins a running thread. The protocol has no atomic loaded-only
        # guard, so an unload between this check and resume remains a fail-closed
        # limitation for callers that require strict ownership guarantees.
        resumed = self.client.request('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
        after = resumed.get('thread') or {}
        if (after.get('id') != thread_id or after.get('cwd') != before.get('cwd') or
                resumed.get('modelProvider') != before.get('modelProvider')):
            raise AppServerUnsupported('CLI thread identity changed during join')
        with self.lock:
            self.joined[thread_id] = self.client.process
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
        with self.lock:
            pending = list(self.requests.get(thread_id, {}).values())
            state = self._state(thread, pending, self.metadata[thread_id])
            self.states[thread_id] = state
        self.on_state(thread_id, copy.deepcopy(state))
        return copy.deepcopy(state)

    def call(self, thread_id, desktop_method, params, timeout=30):
        self._require_joined(thread_id)
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
            with self.lock:
                pending = self.requests.get(thread_id, {}).get(request_id)
            if not pending or pending['method'] != approval_methods[desktop_method]:
                raise AppServerUnavailable('CLI approval is no longer pending')
            if desktop_method in ('thread-follower-command-approval-decision',
                                  'thread-follower-file-approval-decision'):
                result = {'decision': params['decision']}
            else:
                result = params['response']
            self.client.respond(request_id, result)
            with self.lock:
                self.requests[thread_id].pop(request_id, None)
            self._queue_refresh(thread_id)
            return {'submitted': True}
        raise AppServerUnsupported('CLI operation is not supported by this adapter')

    @staticmethod
    def _inputs(inputs, context):
        result = []
        for value in inputs:
            kind = value.get('type')
            if kind in ('text', 'localImage', 'skill'):
                result.append({key: value[key] for key in ('type', 'text', 'path', 'name') if key in value})
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

    def _event(self, event):
        thread_id = (event.get('params') or {}).get('threadId')
        with self.lock:
            joined = thread_id in self.joined
        if joined:
            self._queue_refresh(thread_id)

    def _server_request(self, request):
        thread_id = (request.get('params') or {}).get('threadId')
        with self.lock:
            if thread_id not in self.joined:
                return
            self.requests.setdefault(thread_id, {})[request['id']] = request
        self._queue_refresh(thread_id)

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
                pass
            finally:
                with self.lock:
                    self.refreshing.discard(thread_id)
                    self.refresh_pending.discard(thread_id)
        threading.Thread(target=run, daemon=True).start()

    def _disconnect(self):
        with self.lock:
            self.joined.clear()
            self.metadata.clear()
            self.states.clear()
            self.requests.clear()
            self.refresh_pending.clear()
        self.on_disconnect()

    def close(self):
        self.client.close()
        self._disconnect()
