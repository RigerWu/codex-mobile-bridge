"""Guarded local CLI account selection; never touches SSH profiles."""

import hashlib
import json
import socket
from pathlib import Path

from bridge.clients.desktop_app import DesktopApp
from bridge.features.accounts.accounts import ManagedRPC, private_bytes, private_json, token_owner


class LocalCliSync:
    def __init__(self, bridge):
        self.bridge = bridge
        self._claude_backup = None

    def _accounts(self):
        return self.bridge.accounts

    def _codex_runtime(self):
        accounts = self._accounts()
        executable = accounts.index.get('desktopExecutable') or DesktopApp.discover(accounts.runtime)
        app = DesktopApp(executable, accounts.home) if executable else None
        return bool(app and app.processes()), app

    def _codex_idle(self):
        cli = self.bridge.cli_sessions
        with cli.lock:
            if cli.joined or cli.attaching or cli.requests:
                raise ValueError('请先结束或断开本机 Codex CLI 会话后再切换接入')
        if self._accounts().blockers():
            raise ValueError('有任务或待发送消息，请先处理后再切换接入')
        path = Path(cli.client.socket_path)
        if path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(.25)
                try:
                    probe.connect(str(path))
                except (OSError, TimeoutError):
                    pass  # Stale socket is not a running daemon.
                else:
                    raise ValueError('本机 Codex CLI 后台服务仍在运行，请先正常结束后再切换')

    def _claude_dir(self, row):
        path = Path(row['directory']).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError('Claude Code 配置目录不可用')
        if row['kind'] == 'api':
            base = (self.bridge.data_dir/'cli-access'/'claude').resolve()
            if path.parent != base or path.name != row['id']:
                raise ValueError('Claude API 配置目录不属于本机接入管理器')
        return path

    def preflight(self, provider):
        if provider == 'codex':
            running, _ = self._codex_runtime()
            self._codex_idle()
            return {'sharedWithDesktop': True, 'desktopRunning': running,
                    'needsDesktopSwitch': running}
        if provider == 'claude':
            adapter = self.bridge.desktop_sessions.adapters['claude'] if hasattr(self.bridge, 'desktop_sessions') else None
            if adapter:
                with adapter.cli.lock:
                    if any(owner.process.poll() is None for owner in adapter.cli.owned.values()):
                        raise ValueError('请先结束已继续的本机 Claude Code 会话再切换接入')
            return {'sharedWithDesktop': False, 'desktopRunning': False}
        raise ValueError('不支持的本机 CLI 客户端')

    def snapshot(self, provider):
        if provider == 'codex':
            accounts = self._accounts()
            return {'files': accounts.snapshot_files(), 'activeId': accounts.index.get('activeId')}
        if provider == 'claude':
            return {}  # Profile selection is held by CliAccess; native profiles are read-only.
        raise ValueError('不支持的本机 CLI 客户端')

    def apply(self, provider, row):
        if provider == 'codex':
            accounts = self._accounts()
            current = accounts.info.current()
            if current.get('id') == row['id'] and accounts.active_matches():
                return {'status': 'applied', 'sharedWithDesktop': True}
            running, _ = self._codex_runtime()
            if running:
                raise ValueError('Codex Desktop 正在运行，请先通过桌面账号切换流程')
            self._codex_idle()
            with accounts.gate, accounts.lock:
                accounts.check_ready()
                accounts.idle()
                before = accounts.snapshot_files()
                old_id = accounts.index.get('activeId')
                folder = accounts.prepare(row, before)
                try:
                    private_bytes(accounts.home/'config.toml', (folder/'config.toml').read_bytes())
                    private_bytes(accounts.home/'auth.json', (folder/'auth.json').read_bytes())
                    self.verify(provider, row)
                    accounts.mark_active(row['id'])
                except Exception:
                    accounts.restore_files(before)
                    if old_id:
                        accounts.mark_active(old_id)
                    else:
                        accounts.index['activeId'] = None
                        accounts.index['activeStamp'] = None
                        accounts.save()
                    raise
                finally:
                    import shutil
                    shutil.rmtree(folder, ignore_errors=True)
            return {'status': 'applied', 'sharedWithDesktop': True}
        if provider == 'claude':
            directory = self._claude_dir(row)
            if row['kind'] == 'native':
                return {'status': 'selected', 'directory': str(directory)}
            if row['kind'] != 'api':
                raise ValueError('不支持的 Claude Code 接入')
            key = json.loads((directory/'api.json').read_text())['key']
            if not isinstance(key, str) or not key:
                raise ValueError('Claude Code API Key 不可用')
            env = {'ANTHROPIC_BASE_URL': row['baseUrl']}
            env['ANTHROPIC_AUTH_TOKEN' if row.get('authScheme') == 'bearer' else 'ANTHROPIC_API_KEY'] = key
            if row.get('model'):
                env['ANTHROPIC_MODEL'] = row['model']
            settings = directory/'settings.json'
            self._claude_backup = (settings, settings.read_bytes() if settings.is_file() else None)
            private_json(directory/'settings.json', {'env': env})
            return {'status': 'applied', 'directory': str(directory)}
        raise ValueError('不支持的本机 CLI 客户端')

    def verify(self, provider, row):
        if provider == 'codex':
            accounts = self._accounts()
            with ManagedRPC(accounts.home, accounts.runtime) as rpc:
                config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
                auth = rpc.request('account/read', {'refreshToken': False}).get('account') or {}
            expected = 'openai' if row['kind'] == 'chatgpt' else 'bridge_api'
            if (config.get('model_provider') or 'openai') != expected:
                raise ValueError('本机 Codex CLI 模型提供商未生效')
            if row['kind'] == 'chatgpt':
                tokens = json.loads((accounts.home/'auth.json').read_text()).get('tokens') or {}
                if auth.get('email') != row.get('email') or token_owner({'tokens': tokens}) != row.get('tokenOwner'):
                    raise ValueError('本机 Codex CLI 账号身份未生效')
            else:
                provider_config = (config.get('model_providers') or {}).get('bridge_api') or {}
                secret = json.loads((accounts.directory(row['id'])/'api.json').read_text())['key']
                actual = json.loads((accounts.home/'auth.json').read_text()).get('OPENAI_API_KEY')
                if (auth.get('type') != 'apiKey' or provider_config.get('base_url') != row['baseUrl']
                        or config.get('model') != row['model'] or actual != secret):
                    raise ValueError('本机 Codex CLI API 接入未生效')
            return {'status': 'verified', 'sharedWithDesktop': True}
        if provider == 'claude':
            directory = self._claude_dir(row)
            if row['kind'] == 'native':
                from bridge.clients.claude.cli_session import _auth_ready, _profile
                installed = _profile()
                if not installed or not _auth_ready({**installed, 'directory': directory}):
                    raise ValueError('原 Claude Code CLI 登录未通过核验')
                return {'status': 'verified', 'directory': str(directory),
                        'authentication': 'native-login'}
            key = json.loads((directory/'api.json').read_text())['key']
            settings = json.loads((directory/'settings.json').read_text())
            env = settings.get('env') or {}
            credential = 'ANTHROPIC_AUTH_TOKEN' if row.get('authScheme') == 'bearer' else 'ANTHROPIC_API_KEY'
            if (env.get('ANTHROPIC_BASE_URL') != row['baseUrl'] or env.get(credential) != key
                    or row.get('model') and env.get('ANTHROPIC_MODEL') != row['model']):
                raise ValueError('Claude Code CLI API 配置未生效')
            return {'status': 'verified', 'directory': str(directory),
                    'authentication': 'configured'}
        raise ValueError('不支持的本机 CLI 客户端')

    def restore(self, provider, snapshot):
        if provider == 'codex':
            accounts = self._accounts()
            self._codex_idle()
            with accounts.gate, accounts.lock:
                accounts.restore_files(snapshot['files'])
                if snapshot.get('activeId'):
                    accounts.mark_active(snapshot['activeId'])
                else:
                    accounts.index['activeId'] = None
                    accounts.index['activeStamp'] = None
                    accounts.save()
            return {'status': 'restored'}
        if provider == 'claude':
            if self._claude_backup:
                path, content = self._claude_backup
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    private_bytes(path, content)
                self._claude_backup = None
            return {'status': 'restored'}
        raise ValueError('不支持的本机 CLI 客户端')

    def import_desktop_claude(self, desktop_id, store=None):
        """Map a verified Desktop API profile; never transfer Desktop OAuth cookies."""
        if store is None:
            manager = getattr(self.bridge, 'desktop_sessions', None)
            if manager is None:
                raise ValueError('Claude Desktop 账号管理器不可用')
            store = manager._account_store('claude')
        current = store.public()
        if current.get('activeId') != desktop_id or current.get('current', {}).get('kind') != 'api':
            raise ValueError('仅可导入已生效的 Claude Desktop API 接入；官方登录不能复制到 CLI')
        saved, path = store._account(desktop_id)
        if saved['kind'] != 'api':
            raise ValueError('Claude Desktop 官方登录不能复制到 CLI')
        gateway = json.loads((path/'gateway.json').read_text())
        config = gateway['config']
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = json.loads((path/'manifest.json').read_text())
        if digest != saved.get('fingerprint') or manifest.get('fingerprint') != digest:
            raise ValueError('Claude Desktop API 快照与当前接入不一致')
        auth = config.get('inferenceGatewayAuthScheme')
        key = config.get('inferenceGatewayApiKey')
        models = config.get('inferenceModels') or []
        model = models[0] if models else ''
        if isinstance(model, dict):
            model = model.get('name', '')
        if not (isinstance(key, str) and key and auth in ('bearer', 'x-api-key')
                and isinstance(model, str) and isinstance(config.get('inferenceGatewayBaseUrl'), str)):
            raise ValueError('Claude Desktop API 配置不能转换为本机 CLI 接入')
        return {'name': saved['name'], 'baseUrl': config['inferenceGatewayBaseUrl'],
                'model': model, 'apiKey': key, 'authScheme': auth}
