"""Read Windows process identity in Claude Code 2.1.168's native registry format."""

import re
import subprocess


def process_start_ticks(pid):
    """Return .NET ticks, None for a confirmed absent PID, or raise on uncertainty."""
    if type(pid) is not int or pid <= 0:
        raise OSError('invalid process identity')
    script = ("$ErrorActionPreference='Stop'; "
              f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}" -ErrorAction Stop; '
              "if ($null -eq $p) { 'missing' } else { $p.CreationDate.Ticks }")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError('process identity unavailable') from exc
    if result.returncode != 0:
        raise OSError('process identity unavailable')
    value = result.stdout.strip()
    if value == 'missing':
        return None
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise OSError('process identity unavailable')
    return value
