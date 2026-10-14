"""Reject a Claude CLI resume while its saved session has a live local writer."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable


_UNKNOWN = '无法确认原 Claude Code 会话是否仍在其他终端运行，请在电脑端检查后重试'
_RUNNING = '原 Claude Code 会话仍在其他终端运行，请先在原终端结束会话'


def _pid_domain():
    if sys.platform == 'darwin':
        return 'darwin'
    if sys.platform == 'linux':
        try:
            machine = Path('/etc/machine-id').read_text().strip()
            namespace = os.readlink('/proc/self/ns/pid')
        except OSError as exc:
            raise BridgeUnavailable(_UNKNOWN) from exc
        if machine and re.fullmatch(r'pid:\[\d+\]', namespace):
            return 'darwin:' + machine + ':' + namespace
    raise BridgeUnavailable(_UNKNOWN)


def _native_writer(pid, proc_start):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc
    try:
        env = os.environ.copy()
        env['LC_ALL'] = 'C'
        env['TZ'] = 'UTC'
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                capture_output=True, text=True, timeout=3, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise BridgeUnavailable(_UNKNOWN)
    return result.stdout.split() == proc_start.split()


def assert_session_closed(config_dir: Path, session_id: str = None):
    registry = Path(config_dir)/'sessions'
    if session_id is None and not registry.exists() and not registry.is_symlink():
        return  # A fresh native profile has no sessions yet.
    if not registry.is_dir() or registry.is_symlink():
        raise BridgeUnavailable(_UNKNOWN)
    try:
        domain = _pid_domain()
        entries = list(registry.iterdir())
        for path in entries:
            if path.suffix != '.json':
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                raise BridgeUnavailable(_UNKNOWN)
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                raise BridgeUnavailable(_UNKNOWN)
            if session_id is not None and entry.get('sessionId') != session_id:
                continue
            pid = entry.get('pid')
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or
                    path.stem != str(pid) or not isinstance(entry.get('procStart'), str) or
                    not entry['procStart'] or entry.get('pidDomain') != domain):
                raise BridgeUnavailable(_UNKNOWN)
            if _native_writer(pid, entry['procStart']):
                raise BridgeUnavailable(_RUNNING)
    except (OSError, ValueError, UnicodeError) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc


def assert_profile_idle(config_dir: Path):
    """Use the resume guard against every writer in a native config profile."""
    assert_session_closed(config_dir)
