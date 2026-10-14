"""Gateway-owned saved access for local Codex and Claude Code CLI runtimes."""

import hashlib
import json
import re
import threading
import uuid
from contextlib import ExitStack
from pathlib import Path

from bridge.clients.claude.cli_session import _auth_ready, _profile
from bridge.features.accounts.accounts import private_json
from bridge.features.notifications.channels import read_json


class CliAccess:
    def __init__(self, bridge):
        self.bridge = bridge
        self.root = bridge.data_dir/'cli-access'
        if self.root.is_symlink():
            raise ValueError('CLI 接入目录不能是符号链接')
        self.lock = threading.RLock()
        self.codex_switch = None
        self.claude_verification = None
        self.index = read_json(self.root/'index.json', {'claude': [], 'activeClaude': None})

    def _save(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(self.root/'index.json', self.index)

    def _claude_row(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('CLI 接入标识无效')
        row = next((row for row in self.index['claude'] if row['id'] == identifier), None)
        if row is None:
            raise ValueError('CLI 接入不存在，请刷新列表')
        return row

    def claude_profile(self):
        """The unmodified native resolver is the default and remains testable."""
        with self.lock:
            identifier = self.index.get('activeClaude')
            row = self._claude_row(identifier) if identifier else None
            native = _profile()
            if row is None or native is None:
                return native
            directory = Path(row['directory'])
            if row['kind'] == 'native':
                try:
                    if directory.samefile(native['directory']):
                        return native
                except OSError:
                    pass
            try:
                digest = hashlib.sha256((directory/'settings.json').read_bytes()).hexdigest()
            except OSError:
                digest = 'none'
            return {**native, 'directory': directory,
                    'origin': row['originBase']+':'+digest,
                    'managedApi': row['kind'] == 'api'}

    def _claude_cli(self):
        manager = getattr(self.bridge, 'desktop_sessions', None)
        return manager.adapters['claude'].cli if manager else None

    def public(self):
        with self.lock:
            accounts = self.bridge.accounts
            codex = accounts.public()
            native = _profile()
            selected = self.claude_profile()
            rows = [{key: row[key] for key in ('id', 'name', 'kind', 'baseUrl', 'model', 'authScheme') if key in row}
                    | {'active': row['id'] == self.index.get('activeClaude'),
                       'keySaved': row['kind'] == 'api' and Path(row['secretPath']).is_file()}
                    for row in self.index['claude']]
            return {'codex': {'accounts': codex['accounts'], 'activeId': codex['activeId'],
                              'activeVerified': codex['activeVerified'], 'sharedWithDesktop': True,
                              'switch': codex['switch'], 'cliSwitch': self.codex_switch, 'blockers': codex['blockers']},
                    'claude': {'accounts': rows, 'activeId': self.index.get('activeClaude'),
                               'nativeDirectory': str(native['directory']) if native else None,
                               'selectedDirectory': str(selected['directory']) if selected else None,
                               'available': bool(selected), 'sharedWithDesktop': False,
                               'verification': (self.claude_verification['result']
                                                if self.claude_verification and selected
                                                and self.claude_verification['origin'] == selected['origin'] else None)}}

    @staticmethod
    def _name(value):
        name = value.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('请填写 1–80 字的接入名称')
        return name.strip()

    def _claude_api(self, value):
        from bridge.features.accounts.accounts import Accounts
        name = self._name(value)
        url = Accounts.api_url(value.get('baseUrl'))
        model = value.get('model', '')
        if not isinstance(model, str) or len(model) > 200 or any(c in model for c in '\r\n\x00'):
            raise ValueError('模型 ID 无效')
        scheme = value.get('authScheme', 'x-api-key')
        if scheme not in ('x-api-key', 'bearer'):
            raise ValueError('Claude API 认证方式无效')
        existing = self._claude_row(value['id']) if value.get('id') else None
        if existing and (existing['kind'] != 'api' or existing['id'] == self.index.get('activeClaude')):
            raise ValueError('请先切换到其他接入，再编辑当前 API 配置')
        identifier = existing['id'] if existing else uuid.uuid4().hex
        native = _profile()
        if native is None:
            raise ValueError('未找到 Claude Code CLI')
        directory = Path(existing['directory']) if existing else native['directory']
        fresh_default = (not directory.exists() and not directory.is_symlink()
                         and native['origin'].startswith('default:')
                         and directory == native['directory'])
        if not directory.is_dir() and not fresh_default:
            raise ValueError('Claude Code 配置目录不可用')
        private_dir = self.root/'claude'/identifier
        if private_dir.is_symlink():
            raise ValueError('CLI 接入目录不能是符号链接')
        secret_path = private_dir/'api.json'
        key = value.get('apiKey', '')
        if not key and existing:
            key = read_json(secret_path, {}).get('key', '')
        if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(c in key for c in '\r\n\x00'):
            raise ValueError('请填写有效 API Key')
        private_json(secret_path, {'key': key})
        row = {'id': identifier, 'name': name, 'kind': 'api', 'baseUrl': url,
               'model': model, 'authScheme': value.get('authScheme', 'x-api-key'),
               'directory': str(directory), 'secretPath': str(secret_path),
               'originBase': 'gateway:'+identifier+':'+str(directory)}
        if existing:
            existing.update(row)
        else:
            self.index['claude'].append(row)
        self._save()
        return identifier

    def _import_claude(self, value):
        name = self._name(value)
        native = _profile()
        if native is None:
            raise ValueError('未找到 Claude Code CLI')
        source = value.get('directory') or str(native['directory'])
        if not isinstance(source, str) or not Path(source).expanduser().is_absolute():
            raise ValueError('请填写本机 Claude 配置目录的完整路径')
        directory = Path(source).expanduser().resolve(strict=True)
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('Claude 配置目录不可用')
        profile = {**native, 'directory': directory}
        if not _auth_ready(profile):
            raise ValueError('此 Claude 配置目录尚未完成登录')
        row = next((row for row in self.index['claude'] if row['kind'] == 'native' and row['directory'] == str(directory)), None)
        if row:
            row['name'] = name
        else:
            identifier = uuid.uuid4().hex
            origin = native['origin'].rsplit(':', 1)[0] if directory.samefile(native['directory']) else 'native:'+str(directory)
            self.index['claude'].append({'id': identifier, 'name': name, 'kind': 'native',
                                         'directory': str(directory), 'originBase': origin})
        self._save()

    def change(self, value):
        if not isinstance(value, dict) or value.get('provider') not in ('codex', 'claude'):
            raise ValueError('CLI 接入请求无效')
        provider, action = value['provider'], value.get('action')
        saved_id = None
        with ExitStack() as locks:
            # ClaudeCli.call holds cli.lock while resolving this manager's
            # profile. Keep the same order through the settings transaction.
            if provider == 'claude' and action == 'switch':
                cli = self._claude_cli()
                if cli:
                    locks.enter_context(cli.lock)
            locks.enter_context(self.lock)
            if provider == 'codex':
                accounts = self.bridge.accounts
                if action == 'saveApi':
                    request = dict(value)
                    if request.get('id') and not request.get('apiKey'):
                        request['apiKey'] = read_json(accounts.directory(request['id'])/'api.json', {}).get('key', '')
                    accounts.add_api(request)
                elif action == 'importCurrent':
                    accounts.scan({'source': str(accounts.home)})
                    identity = accounts.info.read_identity()
                    candidate = next((row for row in accounts.discovery['candidates']
                                      if row['canImport'] and not row['imported']
                                      and row['kind'] == identity.get('kind')
                                      and (row['kind'] == 'chatgpt' or
                                           (row['baseUrl'] == identity.get('baseUrl')
                                            and row['model'] == identity.get('model')
                                            and accounts.candidates[row['id']]['key'] == identity.get('key')))), None)
                    if not candidate:
                        raise ValueError('当前 Codex 配置没有可导入的登录或 API 接入')
                    accounts.import_account({'candidateId': candidate['id'], 'name': value.get('name') or candidate['name']})
                elif action in ('rename', 'delete'):
                    accounts.control({'action': action, 'id': value.get('id'), 'name': value.get('name')})
                elif action == 'switch':
                    return self._switch(provider, accounts.row(value.get('id')), value)
                else:
                    raise ValueError('不支持的 CLI 接入操作')
            elif action == 'saveApi':
                saved_id = self._claude_api(value)
            elif action == 'importCurrent':
                self._import_claude(value)
            elif action == 'importDesktop':
                from bridge.features.accounts.cli_sync import LocalCliSync
                settings = LocalCliSync(self.bridge).import_desktop_claude(value.get('desktopId'))
                saved_id = self._claude_api(settings)
            elif action == 'rename':
                self._claude_row(value.get('id'))['name'] = self._name(value)
                self._save()
            elif action == 'delete':
                row = self._claude_row(value.get('id'))
                if row['id'] == self.index.get('activeClaude'):
                    raise ValueError('请先切换到其他接入，再删除当前配置')
                self.index['claude'].remove(row)
                self._save()
                if row['kind'] == 'api':
                    Path(row['secretPath']).unlink(missing_ok=True)
                    Path(row['secretPath']).parent.rmdir()
            elif action == 'switch':
                return self._switch(provider, self._claude_row(value.get('id')), value)
            else:
                raise ValueError('不支持的 CLI 接入操作')
            result = self.public()
            if saved_id:
                result['savedId'] = saved_id
            return result

    def control(self, value):
        return self.public() if value.get('action') == 'list' else self.change(value)

    def _switch(self, provider, row, value):
        if value.get('confirmed') is not True:
            raise ValueError('请确认切换此电脑的 CLI 接入')
        if provider == 'claude' and _profile() is None:
            raise ValueError('未找到 Claude Code CLI')
        from bridge.features.accounts.cli_sync import LocalCliSync
        sync = LocalCliSync(self.bridge)
        preflight = sync.preflight(provider)
        if provider == 'codex' and preflight['desktopRunning']:
            if value.get('desktopRestartConfirmed') is not True or value.get('tasksConfirmed') is not True:
                raise ValueError('请确认桌面任务已结束并同意重启 Codex Desktop')
            accounts = self.bridge.accounts
            request_id = str(uuid.uuid4())
            accounts.switch({'id': row['id'], 'requestId': request_id,
                             'confirmed': True, 'tasksConfirmed': True})
            self.codex_switch = {'phase': 'pending', 'targetId': row['id']}
            threading.Thread(target=self._verify_codex_desktop_switch,
                             args=(row['id'], accounts.thread), daemon=True).start()
            return self.public()
        if provider == 'codex':
            accounts = self.bridge.accounts
            with accounts.gate, self.bridge.account.lock:
                accounts.check_ready()
                before = sync.snapshot(provider)
                applied = None
                try:
                    applied = sync.apply(provider, row)
                    sync.verify(provider, row)
                except Exception:
                    # apply() handles its own partial-write rollback. A
                    # prewrite rejection must not restore an older snapshot
                    # over another account transaction.
                    if applied and applied.get('mutated') is True:
                        sync.restore(provider, before)
                    raise
            return self.public()
        cli = self._claude_cli() if provider == 'claude' else None
        before = sync.snapshot(provider)
        previous_claude = self.index.get('activeClaude')
        previous_verification = self.claude_verification
        try:
            sync.apply(provider, row)
            verification = sync.verify(provider, row)
            if provider == 'claude':
                self.index['activeClaude'] = row['id']
                self._save()
                self.claude_verification = {'result': verification if isinstance(verification, dict) else None,
                                            'origin': self.claude_profile()['origin']}
                if cli:
                    cli.auth_cache = None
        except Exception:
            pointer_changed = provider == 'claude' and self.index.get('activeClaude') != previous_claude
            if pointer_changed:
                self.index['activeClaude'] = previous_claude
            self.claude_verification = previous_verification
            sync.restore(provider, before)
            if pointer_changed:
                self._save()
            raise
        return self.public()

    def _verify_codex_desktop_switch(self, identifier, thread):
        thread.join(timeout=180)
        with self.lock:
            if not self.codex_switch or self.codex_switch.get('targetId') != identifier:
                return
            accounts = self.bridge.accounts
            if thread.is_alive() or accounts.state.get('phase') != 'complete':
                self.codex_switch = {'phase': 'failed', 'targetId': identifier,
                                     'error': accounts.state.get('error') or '桌面账号切换未完成'}
                return
            try:
                from bridge.features.accounts.cli_sync import LocalCliSync
                sync = LocalCliSync(self.bridge)
                row = accounts.row(identifier)
                sync.apply('codex', row)
                sync.verify('codex', row)
                self.codex_switch = {'phase': 'verified', 'targetId': identifier}
            except Exception as exc:
                self.codex_switch = {'phase': 'failed', 'targetId': identifier, 'error': str(exc)}
