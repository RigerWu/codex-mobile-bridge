"""Read Windows process identities in native Claude Code registry formats."""

import ctypes
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


def process_start_filetime(pid):
    """Read Claude Code 2.1.287's raw Windows creation FILETIME."""
    if type(pid) is not int or pid <= 0:
        raise OSError('invalid process identity')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p, *([ctypes.POINTER(ctypes.c_uint64)] * 4)]
    kernel.GetProcessTimes.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID has exited.
            return None
        raise OSError('process identity unavailable')
    try:
        times = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise OSError('process identity unavailable')
        return str(times[0].value)
    finally:
        kernel.CloseHandle(handle)


def pid_domain():
    """Match Claude Code 2.1.287's lowercased Windows machine ID domain."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
            machine, kind = winreg.QueryValueEx(key, 'MachineGuid')
    except OSError as exc:
        raise OSError('machine identity unavailable') from exc
    if kind != winreg.REG_SZ or not isinstance(machine, str) or not machine or machine != machine.strip():
        raise OSError('machine identity unavailable')
    return 'darwin:' + machine.lower()
