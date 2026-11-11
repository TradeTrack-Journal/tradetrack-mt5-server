"""Lossless deduplication of immutable, deferred vendor update archives only.

Never traverse liveupdate, account bases, logs or configuration. All original
paths and bytes remain readable; identical files share a read-only NTFS object.
Copy an archived file out before editing/using it for an installation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from uuid import uuid4

ARCHIVE = re.compile(r'liveupdate-(?:deferred|(?:orphan-)?held)-[0-9-]+$')
PACKAGE = re.compile(r'(?:mt5(?:clw64|clwide64|clwtst64|onnx64)\.[0-9]{4,6}|terminal64\.exe)$')


def safe_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('ARCHIVE_PATH_OUTSIDE_ROOT')
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part, 'is_junction', lambda: False)()):
            raise ValueError('ARCHIVE_REPARSE_POINT')
    return path


@contextmanager
def locked_reader(path):
    if os.name != 'nt':
        with Path(path).open('rb') as stream:
            yield stream
        return
    # Allow readers and atomic rename, but deny existing/new writers. An active
    # installer holding incompatible sharing causes a skip, never a forced stop.
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 5, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(fd, 'rb') as stream:
        yield stream


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def digest(stream):
    stream.seek(0)
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace_archive_link(temporary, destination):
    if os.name != 'nt':
        return os.replace(temporary, destination)
    # MoveFileEx (os.replace) refuses an open destination even with DELETE
    # sharing. ReplaceFile keeps the write-denying source handle pinned and
    # preserves the destination's metadata. No sharing or integrity check is lost.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    kernel.ReplaceFileW.restype = wintypes.BOOL
    if not kernel.ReplaceFileW(str(destination), str(temporary), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def candidates(root, now, minimum_age=3600):
    root = safe_path(root, root)
    for profile in sorted(root.iterdir()):
        if not re.fullmatch(r'[A-Fa-f0-9]{32}', profile.name):
            continue
        safe_path(profile, root)
        if not profile.is_dir():
            continue
        for archive in sorted(profile.iterdir()):
            if not ARCHIVE.fullmatch(archive.name):
                continue
            safe_path(archive, root)
            stamp = re.search(r'([0-9]{16,20})$', archive.name)
            if stamp:
                archived_at = int(stamp[1]) / 1e9
            else:
                stamp = re.search(r'([0-9]{8})(?:-([0-9]{6}))?$', archive.name)
                if not stamp:
                    continue
                archived_at = datetime.strptime(stamp[1] + (stamp[2] or '000000'), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
            if not archive.is_dir() or now - archived_at < minimum_age:
                continue
            # Updater payloads are either directly in the archive or its temp
            # directory. Deliberately do not recurse into unknown directories.
            for folder in (archive, archive / 'temp'):
                safe_path(folder, root)
                if not folder.is_dir():
                    continue
                for path in sorted(folder.iterdir()):
                    if not PACKAGE.fullmatch(path.name):
                        continue
                    safe_path(path, root)
                    info = path.stat()
                    if stat.S_ISREG(info.st_mode) and now - info.st_mtime >= minimum_age:
                        yield path


def deduplicate(root, store, *, max_seconds=240, max_files=2000, progress=None):
    root, store = safe_path(root, root), safe_path(store, store)
    if store == root or store.is_relative_to(root) or root.is_relative_to(store):
        raise ValueError('ARCHIVE_STORE_OVERLAP')
    objects = safe_path(store / 'objects', store)
    objects.mkdir(parents=True, exist_ok=True)
    if root.stat().st_dev != objects.stat().st_dev:
        raise ValueError('ARCHIVE_STORE_VOLUME_MISMATCH')
    # Hard-linked paths from previous passes require no content reads. Objects
    # used for a NEW replacement are rehashed under a write-denying handle.
    identities = {}
    verified = {}
    for obj in objects.iterdir():
        if re.fullmatch(r'[a-f0-9]{64}', obj.name):
            safe_path(obj, store)
            info = obj.stat()
            if stat.S_ISREG(info.st_mode):
                identities[(info.st_dev, info.st_ino)] = obj
    result = {'examined': 0, 'linked': 0, 'alreadyLinked': 0, 'newObjects': 0,
              'duplicateLogicalBytes': 0, 'skippedBusy': 0, 'budgetReached': False}
    began = time.monotonic()
    for path in candidates(root, time.time()):
        if (time.monotonic() - began >= max_seconds or
                result['linked'] + result['newObjects'] + result['skippedBusy'] >= max_files):
            result['budgetReached'] = True
            break
        result['examined'] += 1
        retained = None
        try:
            info = path.stat()
            if (info.st_dev, info.st_ino) in identities:
                result['alreadyLinked'] += 1
                continue
            if info.st_nlink != 1:
                result['skippedBusy'] += 1
                continue  # Unknown shared file may also be used outside this archive.
            with locked_reader(path) as source:
                before = os.fstat(source.fileno())
                key = digest(source)
                if signature(before) != signature(os.fstat(source.fileno())):
                    raise OSError('ARCHIVE_CHANGED_DURING_HASH')
                safe_path(path, root)
                if signature(before) != signature(path.stat()):
                    raise OSError('ARCHIVE_PATH_CHANGED')
                obj = safe_path(objects / key, store)
                if not obj.exists():
                    # Extra link, no copying and no removal of unique bytes.
                    os.link(path, obj)
                    os.chmod(obj, stat.S_IREAD)
                    verified[key] = signature(obj.stat())
                    result['newObjects'] += 1
                else:
                    with locked_reader(obj) as target:
                        target_info = os.fstat(target.fileno())
                        if (target_info.st_size != before.st_size or
                                (verified.get(key) != signature(target_info) and digest(target) != key)):
                            raise ValueError('ARCHIVE_OBJECT_HASH_MISMATCH')
                        verified[key] = signature(target_info)
                        if signature(target_info) != signature(obj.stat()):
                            raise OSError('ARCHIVE_OBJECT_CHANGED')
                        record = {'at': time.time(), 'path': str(path.relative_to(root)),
                                  'sha256': key, 'size': before.st_size, 'mtimeNs': before.st_mtime_ns}
                        # Durable evidence precedes atomic replacement. Failure to
                        # write the manifest leaves the original file untouched.
                        with (store / 'manifest.jsonl').open('a', encoding='utf-8') as journal:
                            journal.write(json.dumps(record) + '\n')
                            journal.flush()
                            os.fsync(journal.fileno())
                        temporary = path.with_name('.dedup-link-' + uuid4().hex)
                        retained = store / ('pending-original-' + uuid4().hex)
                        os.link(path, retained)
                        os.link(obj, temporary)
                        # ReplaceFile must write the replacement's metadata. Close
                        # its read lock for that operation, while the original
                        # remains write-locked AND retained by an extra hard link.
                        target.close()
                        try:
                            if signature(before) != signature(path.stat()):
                                raise OSError('ARCHIVE_PATH_CHANGED')
                            # Windows disallows renaming a read-only source link.
                            # Only archived immutable packages are eligible. The
                            # original is retained until a full post-replace hash.
                            os.chmod(temporary, stat.S_IWRITE | stat.S_IREAD)
                            replace_archive_link(temporary, path)
                        finally:
                            # Only our newly created hard link, never an archive.
                            if temporary.exists():
                                try:
                                    temporary.unlink()
                                except OSError:
                                    pass  # Read-only extra link uses no duplicate data blocks.
                            os.chmod(obj, stat.S_IREAD)
                        if not os.path.samefile(path, obj):
                            raise ValueError('ARCHIVE_LINK_VERIFY_FAILED')
                        os.chmod(obj, stat.S_IREAD)
                        with locked_reader(path) as checked:
                            if digest(checked) != key:
                                raise ValueError('ARCHIVE_POST_REPLACE_HASH_MISMATCH')
                        # The original handle still denies writers; the retained
                        # copy is byte-identical to the now verified linked path.
                        retained.unlink()
                        result['linked'] += 1
                        result['duplicateLogicalBytes'] += before.st_size
                object_info = obj.stat()
                identities[(object_info.st_dev, object_info.st_ino)] = obj
        except OSError as error:
            if retained and retained.exists() and path.exists() and os.path.samefile(path, retained):
                retained.unlink()  # Replacement did not happen; original still exists.
            result['skippedBusy'] += 1
            result['lastSkipError'] = getattr(error, 'winerror', None) or error.errno
        if progress and result['examined'] % 20 == 0:
            progress(dict(result))
    return result
