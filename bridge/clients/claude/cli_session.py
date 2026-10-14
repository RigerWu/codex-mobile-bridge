"""Read saved Claude Code CLI chats and control only processes started here."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from bridge.clients.claude import model
from bridge.clients.claude.session_registry import assert_session_closed
from bridge.clients.errors import BridgeUnavailable


MODES = {'default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions'}
PERMISSION_OPTIONS = (
    ('default', '默认权限', '需要时请求确认'),
    ('acceptEdits', '允许文件编辑', '文件编辑自动允许，其他操作仍按规则确认'),
    ('plan', '计划模式', '先制定计划'),
    ('dontAsk', '不询问', '无法自动允许的操作直接拒绝'),
    ('auto', '自动模式', '由 Claude 判断可自动执行的操作'),
    ('bypassPermissions', '完全访问', '跳过权限确认'),
)


def _skill_metadata(path):
    try:
        if path.is_symlink() or path.stat().st_size > 100_000:
            return None
        source = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    if not source.startswith('---\n'):
        return None
    end = source.find('\n---', 4)
    if end < 0:
        return None
    fields = {}
    for line in source[4:end].splitlines():
        match = re.fullmatch(r'([A-Za-z][A-Za-z-]*):\s*(.*?)\s*', line)
        if match:
            fields[match[1]] = match[2].strip('"\'')
    name = fields.get('name') or path.parent.name
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        return None
    return {'name': name, 'description': fields.get('description', ''),
            'selectable': fields.get('user-invocable', 'true').lower() != 'false'}


def _skill_catalog(profile, cwd):
    """Expose installed SKILL.md files from the same profile and cwd as the owned CLI."""
    directory = profile['directory']
    roots = [(directory/'skills', '')]
    project = Path(cwd)
    enabled_plugins = {}
    for settings in (directory/'settings.json', *(parent/'.claude'/'settings.json' for parent in
                                                    reversed((project, *project.parents))),
                     project/'.claude'/'settings.local.json'):
        try:
            values = json.loads(settings.read_text()).get('enabledPlugins', {})
            if isinstance(values, dict):
                enabled_plugins.update(values)
        except (OSError, ValueError, AttributeError):
            pass
    for parent in reversed((project, *project.parents)):
        roots.append((parent/'.claude'/'skills', ''))
    registry = directory/'plugins'/'installed_plugins.json'
    try:
        plugins = json.loads(registry.read_text()).get('plugins', {})
    except (OSError, ValueError, AttributeError):
        plugins = {}
    if isinstance(plugins, dict):
        for key, entries in plugins.items():
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]+@[A-Za-z0-9_.-]+', key):
                continue
            if enabled_plugins.get(key) is False:
                continue
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, dict) and isinstance(entry.get('installPath'), str):
                    root = Path(entry['installPath'])
                    if root.is_dir() and not root.is_symlink():
                        roots.append((root/'skills', key.split('@', 1)[0] + ':'))
    rows = {}
    for root, namespace in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.glob('*/SKILL.md'):
            if path.parent.is_symlink():
                continue
            metadata = _skill_metadata(path)
            if metadata:
                name = namespace + metadata['name']
                rows[name] = {'id': name, 'name': name, 'description': metadata['description'],
                              'kind': 'skill', 'selectable': metadata['selectable']}
    return sorted(rows.values(), key=lambda row: row['id'])


class ResumePreflightBlocked(BridgeUnavailable):
    """The resume process has definitely not been started."""


def _profile():
    binary = shutil.which('claude')
    if not binary:
        binary = next((str(path) for path in (Path('/opt/homebrew/bin/claude'),
                       Path('/usr/local/bin/claude'), Path.home()/'.local/bin/claude')
                       if path.is_file() and os.access(path, os.X_OK)), None)
    if not binary:
        return None
    explicit = os.environ.get('CLAUDE_CONFIG_DIR')
    if explicit:
        directory = Path(explicit).expanduser().resolve()
        origin = 'environment:' + str(directory)
    else:
        directory = Path.home()/'.claude'
        origin = 'default:' + str(directory)
        cockpit = Path.home()/'.cockpit_tools'
        try:
            selected = json.loads((cockpit/'provider_current_accounts.json').read_text())['current_accounts']['claude_code_account']
            accounts = json.loads((cockpit/'claude_accounts.json').read_text())['accounts']
            instances = json.loads((cockpit/'claude_instances.json').read_text())['instances']
            if any(item.get('id') == selected for item in accounts):
                instance = next(item for item in instances if item.get('bindAccountId') == selected
                                and item.get('launchMode') == 'cli')
                candidate = Path(instance['userDataDir']).expanduser().resolve()
                if (candidate/'settings.json').is_file():
                    directory, origin = candidate, 'cockpit:' + selected + ':' + str(candidate)
        except (OSError, ValueError, KeyError, StopIteration, TypeError):
            pass
    try:
        settings_digest = hashlib.sha256((directory/'settings.json').read_bytes()).hexdigest()
    except OSError:
        settings_digest = 'none'
    return {'binary': binary, 'directory': directory, 'origin': origin + ':' + settings_digest}


def _auth_ready(profile):
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
    try:
        process = subprocess.run([profile['binary'], 'auth', 'status', '--json'],
                                 env=env, capture_output=True, text=True, timeout=5)
        return process.returncode == 0 and json.loads(process.stdout).get('loggedIn') is True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _events(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError('Claude 会话记录过大，无法读取')
    result = []
    with path.open(errors='replace') as source:
        for line in source:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                result.append(event)
    return result


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text')
    return ''


def _saved(path, origin):
    try:
        native = str(uuid.UUID(path.stem))
        events = _events(path)
    except (OSError, ValueError):
        return None
    users = [event for event in events if event.get('type') == 'user' and not event.get('isSidechain')]
    if not users:
        return None
    first = users[0]
    cwd = next((event.get('cwd') for event in reversed(events)
                if event.get('type') in ('user', 'assistant') and isinstance(event.get('cwd'), str)
                and event.get('cwd')), '')
    if not cwd:
        return None
    assistants = [event for event in events if event.get('type') == 'assistant'
                  and isinstance(event.get('message'), dict)]
    selected_model = next((event['message'].get('model') for event in reversed(assistants)
                           if isinstance(event['message'].get('model'), str)), '')
    permission = next((event.get('mode') for event in reversed(events)
                       if event.get('type') == 'mode' and event.get('mode') in MODES), None)
    if permission is None:
        permission = next((event.get('permissionMode') for event in reversed(users)
                           if event.get('permissionMode') in MODES), None)
    message = first.get('message') if isinstance(first.get('message'), dict) else {}
    lines = _text(message.get('content')).strip().splitlines()
    title = lines[0][:120] if lines else 'Claude Code 会话'
    try:
        updated = path.stat().st_mtime
    except OSError:
        return None
    return {'id': model.gateway_id('cli:' + origin, native), 'nativeId': native, 'backend': 'cli', 'mode': 'cli',
            'host': 'local', 'title': title, 'cwd': cwd, 'model': selected_model,
            'permissionMode': permission or 'default', 'permissionKnown': permission is not None,
            'updatedAt': updated, 'archived': False, 'origin': origin, 'path': path,
            'events': events}


class _Owned:
    def __init__(self, process, origin, native, cwd):
        self.process, self.origin, self.native, self.cwd = process, origin, native, cwd
        self.condition = threading.Condition()
        self.responses = {}
        self.pending = {}
        self.events = []
        self.active = False
        self.model = None
        self.permission = None
        self.catalog = {}
        self.error = None
        self.finished = False
        threading.Thread(target=self._read, daemon=True, name='claude-cli-reader').start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                with self.condition:
                    kind = event.get('type')
                    if kind == 'control_response':
                        response = event.get('response') or {}
                        self.responses[response.get('request_id')] = response
                    elif kind == 'control_request' and (event.get('request') or {}).get('subtype') == 'can_use_tool':
                        self.pending[event.get('request_id')] = event
                    elif kind == 'control_cancel_request':
                        self.pending.pop(event.get('request_id'), None)
                    elif kind == 'system' and event.get('subtype') == 'init':
                        if event.get('session_id') != self.native or event.get('cwd') != self.cwd:
                            self.error = 'Claude 会话身份或工作目录不一致'
                            self.process.terminate()
                        self.model = event.get('model') or self.model
                        self.permission = event.get('permissionMode') or self.permission
                    elif kind == 'system' and event.get('subtype') == 'status':
                        if event.get('permissionMode') in MODES:
                            self.permission = event['permissionMode']
                    elif kind == 'result':
                        self.active = False
                        self.pending.clear()
                        self.events.append(event)
                    elif kind == 'assistant':
                        self.events.append(event)
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    def write(self, frame):
        with self.condition:
            if self.process.poll() is not None or self.error:
                raise BridgeUnavailable(self.error or 'Claude Code 会话已关闭，请重新继续')
            try:
                self.process.stdin.write(json.dumps(frame, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise BridgeUnavailable('Claude Code 写入结果不明，请检查会话') from None

    def control(self, request, timeout=20):
        rid = str(uuid.uuid4())
        self.write({'type': 'control_request', 'request_id': rid, 'request': request})
        deadline = time.monotonic() + timeout
        with self.condition:
            while rid not in self.responses and self.process.poll() is None and time.monotonic() < deadline:
                self.condition.wait(max(0, deadline-time.monotonic()))
            response = self.responses.pop(rid, None)
        if not response or response.get('subtype') != 'success':
            raise BridgeUnavailable('Claude Code 控制请求未确认，请检查原会话')
        return response.get('response') or {}

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class ClaudeCli:
    def __init__(self):
        self.lock = threading.RLock()
        self.owned = {}
        self.index = {}
        self.auth_cache = None
        self.closed = False

    def _ready(self, profile, *, fresh=False):
        if self.closed or not profile:
            return False
        now = time.time()
        with self.lock:
            cached = self.auth_cache
            if not fresh and cached and cached[0] == profile['origin'] and cached[1] > now:
                return cached[2]
        ready = _auth_ready(profile)
        with self.lock:
            self.auth_cache = (profile['origin'], now + 15, ready)
        return ready

    def status(self):
        if self.closed:
            return {'cliAvailable': False, 'cliAuthReady': False}
        profile = _profile()
        return {'cliAvailable': bool(profile), 'cliAuthReady': self._ready(profile)}

    def busy(self):
        with self.lock:
            for owned in self.owned.values():
                if owned.process.poll() is None:
                    with owned.condition:
                        if owned.active or owned.pending:
                            return True
            return False

    def listing(self):
        profile = _profile()
        if not profile:
            return {'connected': False, 'sessions': []}
        rows = {}
        root = profile['directory']/'projects'
        if root.is_dir():
            for project in root.iterdir():
                if not project.is_dir() or project.is_symlink():
                    continue
                for path in project.glob('*.jsonl'):
                    if path.is_symlink():
                        continue
                    row = _saved(path, profile['origin'])
                    if row:
                        rows[row['id']] = row
        with self.lock:
            self.index = rows
            public = [self._public(row) for row in rows.values()]
        return {'connected': True, 'sessions': public}

    def _public(self, row):
        value = {key: item for key, item in row.items() if key not in ('events', 'path', 'origin', 'permissionKnown')}
        owned = self.owned.get(row['nativeId'])
        live = owned and owned.origin == row['origin'] and owned.process.poll() is None and not owned.error
        if live:
            with owned.condition:
                pending = dict(owned.pending)
                active = owned.active
        else:
            pending, active = {}, False
        awaiting = bool(pending)
        value.update(status='active' if active else 'idle', runtimeKnown=True,
                     connectionState='awaiting' if awaiting else 'active' if active else 'ready' if live else 'closed',
                     requests=[{'id': rid, 'tool': event['request'].get('tool_name', '工具'),
                                'input': event['request'].get('input', {}),
                                'needsInput': event['request'].get('tool_name') == 'AskUserQuestion'}
                               for rid, event in pending.items()])
        if live:
            value['model'] = owned.model or value['model']
            value['permissionMode'] = owned.permission or value['permissionMode']
        return value

    def _get(self, sid):
        profile = _profile()
        with self.lock:
            cached = self.index.get(sid)
        row = None
        if cached and profile and cached['origin'] == profile['origin'] and not cached['path'].is_symlink():
            row = _saved(cached['path'], profile['origin'])
            if row:
                with self.lock:
                    self.index[sid] = row
        else:
            self.listing()
            with self.lock:
                row = self.index.get(sid)
        if row is None:
            raise ValueError('找不到 Claude Code 会话或原账号配置已切换')
        return row

    def contains(self, sid):
        profile = _profile()
        with self.lock:
            row = self.index.get(sid)
        if row and profile and row['origin'] == profile['origin'] and not row['path'].is_symlink() and row['path'].is_file():
            return True
        self.listing()
        return sid in self.index

    def call(self, action, sid, body):
        try:
            row = self._get(sid)
        except (ValueError, BridgeUnavailable) as exc:
            if action == 'resume':
                raise ResumePreflightBlocked(str(exc)) from exc
            raise
        native = row['nativeId']
        with self.lock:
            owned = self.owned.get(native)
            if owned and owned.error and owned.process.poll() is None:
                raise (ResumePreflightBlocked if action == 'resume' else BridgeUnavailable)(owned.error)
            if owned and owned.origin != row['origin'] and owned.process.poll() is None:
                raise (ResumePreflightBlocked if action == 'resume' else BridgeUnavailable)(
                    '此会话已由另一 Claude Code 配置继续，请先结束该连接')
            if owned and (owned.origin != row['origin'] or owned.process.poll() is not None):
                owned = None
            public = self._public(row)
            live = owned is not None
            if action == 'detail':
                if live:
                    with owned.condition:
                        events = row['events'] + list(owned.events)
                else:
                    events = row['events']
                messages = model.transcript(events)
                unique = {item['id']: item for item in messages}
                can_resume = (not live and row['permissionKnown'] and self._ready(_profile())
                              and Path(row['cwd']).is_dir())
                return {'connected': True, 'session': public, 'messages': list(unique.values()),
                        'turns': model.turns(events),
                        'notice': '无法确认原会话权限模式，请在电脑端继续' if not live and not row['permissionKnown'] else '',
                        'capabilities': {
                            'resume': can_resume,
                            'send': live and public['connectionState'] == 'ready',
                            'models': live, 'permissions': live, 'stop': live,
                            'attachments': False, 'queue': False, 'steer': False, 'skills': live}}
            if action == 'catalog':
                if not live:
                    return {'models': [], 'capabilities': {'models': False}, 'notice': '请先继续这条 Claude Code 会话'}
                models = owned.catalog.get('models') or []
                return {'models': [{'id': item.get('value') or item.get('id'),
                                    'name': item.get('displayName') or item.get('name') or item.get('value') or item.get('id'),
                                    'efforts': []} for item in models if isinstance(item, dict) and (item.get('value') or item.get('id'))],
                        'currentModel': public['model'], 'currentEffort': '', 'capabilities': {'models': True, 'effort': False,
                                                                                             'skills': True},
                        'skills': _skill_catalog(_profile(), row['cwd'])}
            if action == 'access' and 'mode' not in body:
                options = [{'value': mode, 'label': label, 'description': description}
                           for mode, label, description in PERMISSION_OPTIONS]
                return {'status': 'accepted', 'mode': public['permissionMode'],
                        'options': options if live else []}
            if action == 'resume':
                if live:
                    return {'status': 'ready'}
                profile = _profile()
                if not profile or profile['origin'] != row['origin'] or not self._ready(profile, fresh=True):
                    raise ResumePreflightBlocked('原 Claude Code 账号配置已变化或不可用')
                if not row['permissionKnown'] or row['permissionMode'] not in MODES:
                    raise ResumePreflightBlocked('无法确认原会话权限模式，请在电脑端继续')
                try:
                    cwd = str(Path(row['cwd']).resolve(strict=True))
                except OSError as exc:
                    raise ResumePreflightBlocked('原项目目录不可用，请在电脑端检查') from exc
                env = os.environ.copy()
                env['CLAUDE_CONFIG_DIR'] = str(profile['directory'])
                command = [profile['binary'], '-p', '--resume', native, '--input-format', 'stream-json',
                           '--output-format', 'stream-json', '--verbose', '--replay-user-messages',
                           '--setting-sources', 'user,project,local', '--permission-mode', row['permissionMode'],
                           '--permission-prompt-tool', 'stdio', '--permission-prompts', 'host',
                           '--allow-dangerously-skip-permissions']
                if row['model']:
                    command.extend(('--model', row['model']))
                try:
                    assert_session_closed(profile['directory'], native)
                except BridgeUnavailable as exc:
                    raise ResumePreflightBlocked(str(exc)) from exc
                process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
                owned = _Owned(process, profile['origin'], native, cwd)
                try:
                    owned.catalog = owned.control({'subtype': 'initialize', 'hooks': None}, timeout=30)
                    if owned.error:
                        raise BridgeUnavailable(owned.error)
                    owned.permission = owned.catalog.get('current_permission_mode') or row['permissionMode']
                    if owned.permission != row['permissionMode']:
                        raise BridgeUnavailable('Claude Code 未保留原会话权限模式')
                except Exception:
                    owned.close()
                    raise
                self.owned[native] = owned
                return {'status': 'ready'}
            if not live:
                raise BridgeUnavailable('请先继续这条 Claude Code 会话')
            if action == 'send':
                if body.get('resolvedImages') or body.get('mode') in ('queue', 'steer'):
                    raise ValueError('Claude Code CLI 暂不支持此发送选项')
                selected = body.get('plugins') or []
                if not isinstance(selected, list) or len(selected) > 1:
                    raise ValueError('Claude Code CLI 每条消息只能调用一个 Skill')
                if selected:
                    offered = {skill['id'] for skill in _skill_catalog(_profile(), row['cwd'])
                               if skill['selectable']}
                    if selected[0] not in offered:
                        raise ValueError('技能已不可用，请刷新后选择')
                    message = '/' + selected[0] + (' ' + body['text'] if body['text'].strip() else '')
                else:
                    message = body['text']
                with owned.condition:
                    if owned.active or owned.pending:
                        raise BridgeUnavailable('请等待当前 Claude Code 任务结束后再发送')
                    owned.active = True
                    owned.write({'type': 'user', 'message': {'role': 'user', 'content': message}})
            elif action == 'stop':
                owned.control({'subtype': 'interrupt'})
            elif action == 'settings':
                selection = body.get('model')
                offered = {item['id'] for item in self.call('catalog', sid, {}).get('models', [])}
                if selection not in offered:
                    raise ValueError('请选择 Claude Code 提供的模型')
                owned.control({'subtype': 'set_model', 'model': selection})
                owned.model = selection
            elif action == 'access':
                mode = body.get('mode')
                if mode not in MODES:
                    raise ValueError('请选择 Claude Code 提供的权限模式')
                if mode == 'bypassPermissions' and body.get('confirmed') is not True:
                    raise ValueError('完全访问需要明确确认')
                result = owned.control({'subtype': 'set_permission_mode', 'mode': mode})
                if result.get('mode') != mode:
                    raise BridgeUnavailable('Claude Code 未确认权限模式')
                with owned.condition:
                    deadline = time.monotonic() + 5
                    while owned.permission != mode and owned.process.poll() is None and time.monotonic() < deadline:
                        owned.condition.wait(max(0, deadline-time.monotonic()))
                    if owned.permission != mode:
                        raise BridgeUnavailable('Claude Code 尚未报告生效的权限模式，请刷新检查')
            elif action == 'respond':
                rid = body.get('requestId')
                with owned.condition:
                    pending = owned.pending.get(rid)
                if not pending:
                    raise ValueError('请求已处理或过期')
                decision = {'accept': 'allow', 'decline': 'deny'}.get(body.get('decision'))
                if not decision:
                    raise ValueError('请选择本次允许或拒绝')
                response = {'behavior': decision}
                if decision == 'allow':
                    updated = pending['request'].get('input', {})
                    if pending['request'].get('tool_name') == 'AskUserQuestion':
                        updated = dict(updated)
                        answers = {}
                        for index, question in enumerate(updated.get('questions') or []):
                            key = str(question.get('id') or question.get('question') or index)
                            values = ((body.get('answers') or {}).get(key) or {}).get('answers') or []
                            if not values or not all(isinstance(value, str) for value in values):
                                raise ValueError('请回答每一项问题')
                            answers[question['question']] = ', '.join(values)
                        updated['answers'] = answers
                    response['updatedInput'] = updated
                else:
                    response['message'] = '用户拒绝了本次操作'
                # Claim before a potentially ambiguous pipe write. A new HTTP request ID
                # must never replay an approval whose result may already be applied.
                with owned.condition:
                    owned.pending.pop(rid, None)
                owned.write({'type': 'control_response', 'response': {'subtype': 'success',
                             'request_id': rid, 'response': response}})
            else:
                raise ValueError('不支持的 Claude Code 操作')
            return {'status': 'accepted'}

    def close(self):
        with self.lock:
            self.closed = True
            for owned in self.owned.values():
                owned.close()
            self.owned.clear()
