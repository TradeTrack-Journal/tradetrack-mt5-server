"""Recover only a silent, unchanged updater for a missing managed terminal.

Caller holds the slot mutex between jobs. Preserve downloaded files for review;
never touch account data or interrupt a live terminal or a visible update dialog.
"""
import ctypes as c
from contextlib import contextmanager
from ctypes import wintypes as w
from pathlib import Path
import time
import os

from .windows_inventory import InventoryError

_observations = {}
_blocked = {}
_orphans = {}
STALL_SECONDS = 600


def defer_staged_updates(native, executable):
    """Preserve pending updates before an API-fenced unattended restart.

    Caller holds the slot mutex and has freshly verified its API quarantine.
    A forced stop alone is insufficient: MT5 also installs on its next start.
    This is deliberate maintenance deferral, not a claim that files are orphaned.
    Running/inaccessible installers remain untouched; no UAC or ACL changes.
    """
    executable = Path(executable).resolve()
    root = Path(os.environ.get('APPDATA', '')) / 'MetaQuotes' / 'Terminal'
    if native.find_process(str(executable))[0] is not None:
        raise InventoryError('SLOT_MUST_BE_STOPPED')
    for origin in root.glob('*/origin.txt'):
        if origin.is_symlink() or origin.parent.is_symlink() or origin.parent.is_junction():
            raise InventoryError('UPDATE_PATH_UNSAFE')
        with origin.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise InventoryError('UPDATE_PATH_UNSAFE')
        value = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
        if Path(value.strip()).resolve() != executable.parent:
            continue
        directory = origin.parent / 'liveupdate'
        if directory.is_symlink() or directory.is_junction():
            raise InventoryError('UPDATE_PATH_UNSAFE')
        if not directory.exists():
            continue
        updater = directory / 'terminal64.exe'
        if native.find_process(str(updater))[0] is not None:
            raise InventoryError('UPDATE_FILES_BUSY')
        signature = fingerprint(directory, executable)
        package = [Path(path) for path, _, _ in signature if Path(path).is_relative_to(directory)]
        if not package:
            continue
        destination = directory.with_name(f'liveupdate-deferred-{time.time_ns()}')
        if destination.exists() or not destination.is_relative_to(directory.parent):
            raise InventoryError('UPDATE_PATH_UNSAFE')
        # Even a hidden/elevated installer must not be mistaken for absent.
        # Its executable/package locks deny these opens. Retain the handles
        # throughout archival, and move the executable last on partial failure.
        with exclusive_update_files([path for path, _, _ in signature]):
            if (native.find_process(str(executable))[0] is not None
                    or native.find_process(str(updater))[0] is not None
                    or fingerprint(directory, executable) != signature):
                raise InventoryError('UPDATE_FILES_BUSY')
            destination.mkdir()
            package.sort(key=lambda path: path == updater.resolve())
            for path in package:
                target = destination / path.relative_to(directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                path.rename(target)
        print(__import__('json').dumps({'state': 'update_deferred_for_maintenance',
                                       'terminal': str(executable)}), flush=True)


@contextmanager
def exclusive_update_files(paths):
    """Deny read/write opens (including image launch), allow archival rename.

    An elevated/inaccessible updater is not treated as absent: its image or
    package locks prevent these opens. Hold every handle through the rename.
    """
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handles = []
    try:
        for path in paths:
            handle = kernel.CreateFileW(str(path), 0xC0000000, 4, None, 3, 0, None)
            if handle == w.HANDLE(-1).value:
                raise InventoryError('UPDATE_FILES_BUSY')
            handles.append(handle)
        yield
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)


def recover_orphaned_update(native, executable, updater):
    """Archive old, unchanged, exclusively accessible leftovers, never delete.

    Caller holds the slot mutex. Two observations at least a minute apart and
    ten-minute-old files distinguish a dead handoff from a freshly staged update.
    """
    directory = Path(updater).parent
    if directory.name != 'liveupdate' or directory.is_symlink() or directory.is_junction():
        raise InventoryError('UPDATE_PATH_UNSAFE')
    directory = directory.resolve()
    key = str(directory)
    if native.find_process(str(executable))[0] is not None or native.find_process(str(updater))[0] is not None:
        _orphans.pop(key, None)
        return False
    signature = fingerprint(directory, executable)
    now = time.monotonic()
    previous = _orphans.get(key)
    if previous is None or previous[0] != signature:
        _orphans[key] = (signature, now)
        return False
    if now - previous[1] < 60 or any(time.time() - stamp / 1e9 < STALL_SECONDS for _, _, stamp in signature):
        return False
    destination = directory.with_name(f'liveupdate-orphan-held-{time.time_ns()}')
    if not destination.is_relative_to(directory.parent) or destination.exists():
        raise InventoryError('UPDATE_PATH_UNSAFE')
    with exclusive_update_files([path for path, _, _ in signature]):
        if (native.find_process(str(executable))[0] is not None
                or native.find_process(str(updater))[0] is not None
                or fingerprint(directory, executable) != signature):
            return False
        # Windows cannot rename a directory containing open files, even when
        # those files permit DELETE sharing. Rename files individually while
        # retaining the handles. Move terminal64.exe LAST: a partial archival
        # therefore continues to block launch on the next controller pass.
        destination.mkdir()
        package = [Path(path) for path, _, _ in signature if Path(path).is_relative_to(directory)]
        package.sort(key=lambda path: path == Path(updater).resolve())
        for path in package:
            target = destination / path.relative_to(directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
    _orphans.pop(key, None)
    _blocked.pop(str(Path(executable).resolve()), None)
    print(__import__('json').dumps({'state': 'orphaned_update_archived', 'terminal': str(executable)}), flush=True)
    return True


def observe_blocked_update(executable, updater, reason):
    """Surface a persistent update prompt/unknown process without clicking it.

    The worker/watchdog telemetry already deduplicates this code durably across
    slots. No window text, command line or account details leave this boundary.
    """
    key = str(Path(executable).resolve())
    signature = fingerprint(Path(updater).parent, executable)
    now = time.monotonic()
    previous = _blocked.get(key)
    if previous is None or previous[0] != signature:
        _blocked[key] = (signature, now)
    elif now - previous[1] >= STALL_SECONDS:
        raise InventoryError(reason)


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
    if native.find_process(str(executable))[0] is not None:
        _observations.pop(key, None)
        return False
    if any(native.u.IsWindowVisible(h) for h in native.windows(pid)):
        _observations.pop(key, None)
        observe_blocked_update(executable, updater, 'UPDATE_REQUIRES_ATTENTION')
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
