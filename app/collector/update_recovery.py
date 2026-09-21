"""Recover only a silent, unchanged updater for a missing managed terminal.

Caller holds the slot mutex between jobs. Preserve downloaded files for review;
never touch account data or interrupt a live terminal or a visible update dialog.
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import time

from .windows_inventory import InventoryError

_observations = {}
STALL_SECONDS = 600


def fingerprint(directory, executable):
    paths = [Path(executable)]
    for path in directory.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise InventoryError('UPDATE_PATH_UNSAFE')
        if path.is_file():
            paths.append(path)
        if len(paths) > 256:
            raise InventoryError('UPDATE_FILE_LIMIT')
    return tuple(sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths))


def terminate_verified(native, updater, pid, identity):
    # Pin a process handle before verification so PID reuse cannot target another process.
    handle = native.k.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
    if not handle:
        raise InventoryError('UPDATE_PROCESS_UNAVAILABLE')
    try:
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        if not native.k.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kernel), c.byref(user)):
            raise InventoryError('UPDATE_PROCESS_UNAVAILABLE')
        held_identity = f'{pid}:{created.dwHighDateTime << 32 | created.dwLowDateTime}'
        if held_identity != identity or native.process_identity(pid, str(updater)) != identity:
            raise InventoryError('TERMINAL_CHANGED')
        native.k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        if not native.k.TerminateProcess(handle, 1) or native.k.WaitForSingleObject(handle, 5000) != 0:
            raise InventoryError('UPDATE_STOP_FAILED')
    finally:
        native.k.CloseHandle(handle)


def recover_stalled_update(native, executable, updater, pid, identity):
    directory = Path(updater).parent.resolve()
    if directory.name != 'liveupdate':
        raise InventoryError('UPDATE_PATH_UNSAFE')
    key = str(directory)
    if native.find_process(str(executable))[0] is not None or any(
        native.u.IsWindowVisible(h) for h in native.windows(pid)
    ):
        _observations.pop(key, None)
        return False
    signature = (identity, fingerprint(directory, executable))
    now = time.monotonic()
    previous = _observations.get(key)
    if previous is None or previous[0] != signature:
        _observations[key] = (signature, now)
        return False
    if now - previous[1] < STALL_SECONDS:
        return False
    # Recheck immediately before termination while the caller still owns the slot.
    if native.find_process(str(executable))[0] is not None or native.find_process(str(updater)) != (pid, identity):
        return False
    if any(native.u.IsWindowVisible(h) for h in native.windows(pid)) or fingerprint(directory, executable) != signature[1]:
        _observations.pop(key, None)
        return False
    destination = directory.with_name(f'liveupdate-held-{time.time_ns()}')
    if not destination.is_relative_to(directory.parent) or destination.exists():
        raise InventoryError('UPDATE_PATH_UNSAFE')
    terminate_verified(native, updater, pid, identity)
    directory.rename(destination)
    _observations.pop(key, None)
    print(__import__('json').dumps({'state': 'updater_recovered', 'terminal': str(executable)}), flush=True)
    return True
