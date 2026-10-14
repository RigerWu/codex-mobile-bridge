"""Reject a Claude CLI resume while its saved session has a live local writer."""

import json
import os
import subprocess
import sys
from pathlib import Path

from bridge.clients.errors import BridgeUnavailable


_UNKNOWN = '无法确认原 Claude Code 会话是否仍在其他终端运行，请在电脑端检查后重试'
_RUNNING = '原 Claude Code 会话仍在其他终端运行，请先在原终端结束会话'


def _darwin_writer(pid, proc_start):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc
    try:
        env = os.environ.copy()
        env['TZ'] = 'UTC'
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                capture_output=True, text=True, timeout=3, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise BridgeUnavailable(_UNKNOWN)
    return result.stdout.split() == proc_start.split()


def assert_session_closed(config_dir: Path, session_id: str):
    registry = Path(config_dir)/'sessions'
    if not registry.is_dir() or registry.is_symlink():
        raise BridgeUnavailable(_UNKNOWN)
    try:
        entries = list(registry.iterdir())
        for path in entries:
            if path.suffix != '.json':
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                raise BridgeUnavailable(_UNKNOWN)
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                raise BridgeUnavailable(_UNKNOWN)
            if entry.get('sessionId') != session_id:
                continue
            pid = entry.get('pid')
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or
                    path.stem != str(pid) or not isinstance(entry.get('procStart'), str) or
                    not entry['procStart'] or entry.get('pidDomain') != sys.platform):
                raise BridgeUnavailable(_UNKNOWN)
            if sys.platform != 'darwin':
                # Other platforms lack a verified native procStart comparison.
                raise BridgeUnavailable(_UNKNOWN)
            if _darwin_writer(pid, entry['procStart']):
                raise BridgeUnavailable(_RUNNING)
    except (OSError, ValueError, UnicodeError) as exc:
        raise BridgeUnavailable(_UNKNOWN) from exc
