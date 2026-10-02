"""Spread verified catalogue additions between idle managed slots, never credentials."""
from hashlib import sha256
from pathlib import Path
import os
import shutil
import time

from .server_preparation import close_terminal, start_terminal
from .windows_inventory import InventoryError, catalogue_fingerprint
from .terminal_build import VERIFIED_BUILDS


def verified_snapshot(worker):
    snapshot = worker.inventory.last_snapshots.get(worker.slot['id'], {})
    session = worker.inventory.sessions.get(worker.slot['id'], {})
    if (not getattr(worker, 'inspected_at', 0) or time.monotonic() - worker.inspected_at >= 90 or
            snapshot.get('verificationMethod') != 'login_dialog' or
            snapshot.get('status') != 'STARTING' or not snapshot.get('serverNames') or
            snapshot.get('processIdentity') != session.get('processIdentity') or
            not snapshot.get('catalogHash') or snapshot['catalogHash'] != worker.catalog_hash):
        return None
    return snapshot


def choose_pair(workers):
    candidates = [(w, verified_snapshot(w)) for w in workers]
    candidates = [(w, s) for w, s in candidates if s]
    candidates.sort(key=lambda pair: (-len(pair[1]['serverNames']), pair[0].slot['id']))
    for source, source_snapshot in candidates:
        names = set(source_snapshot['serverNames'])
        for target, target_snapshot in reversed(candidates):
            # A strict superset adds names and cannot remove a target's servers.
            # Equal sets need no restart even if MT5 rewrote access-point metadata.
            if set(target_snapshot['serverNames']) < names:
                return source, target, source_snapshot, target_snapshot
    return None


def install_catalogue(worker, data, source_names, source_build):
    """Caller owns an immutable public catalogue, positively verified in MT5."""
    snapshot = verified_snapshot(worker)
    if not snapshot or not set(snapshot['serverNames']).issubset(source_names):
        raise InventoryError('CATALOGUE_WOULD_REMOVE_SERVERS')
    native, slot = worker.windows, worker.slot
    executable = Path(slot['executablePath']).resolve()
    root = Path(slot['dataPath']).resolve()
    if root != executable.parent or root.parent.name != 'slots':
        raise InventoryError('INVALID_SLOT_PATH')
    digest = sha256(data).hexdigest()
    if source_build not in VERIFIED_BUILDS:
        raise InventoryError('TERMINAL_BUILD_UNSUPPORTED')
    if not 0 < len(data) <= 8 * 1024 * 1024:
        raise InventoryError('CATALOGUE_SIZE')
    artifact = root.parent.parent / 'server-catalogs' / digest
    artifact.mkdir(parents=True, exist_ok=True)
    frozen = artifact / 'servers.dat'
    if not frozen.exists():
        with frozen.open('xb') as stream:
            stream.write(data)
    if sha256(frozen.read_bytes()).hexdigest() != digest:
        raise InventoryError('ARTIFACT_HASH_MISMATCH')
    with native.inventory_lock(str(executable)):
        if native.terminal_build(str(executable)) != source_build:
            raise InventoryError('CATALOGUE_BUILD_MISMATCH')
        pid, identity = native.find_process(str(executable))
        if not pid or identity != snapshot['processIdentity'] or catalogue_fingerprint(root) != snapshot['catalogHash']:
            raise InventoryError('TERMINAL_CHANGED')
        session = worker.inventory.sessions[slot['id']]
        # API atomically refuses RUNNING jobs and quarantines the old process.
        worker.client.call(f"/slots/{slot['id']}/prepare", {'generation': session['generation']})
        close_terminal(native, executable)
        if native.find_process(str(executable))[0]:
            raise InventoryError('SLOT_MUST_BE_STOPPED')
        target = root / 'config' / 'servers.dat'
        backup = artifact / (slot['id'] + '-' + str(time.time_ns()) + '.previous.servers.dat')
        shutil.copy2(target, backup)
        temporary = target.with_name('servers.dat.sync.tmp')
        with temporary.open('xb') as stream:
            stream.write(data)
        if sha256(temporary.read_bytes()).hexdigest() != digest:
            raise InventoryError('CATALOG_INSTALL_CONFLICT')
        os.replace(temporary, target)
        started = start_terminal(executable)
        worker.inspected_at = 0
    return {'state': 'CATALOGUE_SYNC_PENDING_VERIFICATION', 'slotId': slot['id'],
            'addedServers': len(set(source_names) - set(snapshot['serverNames'])),
            'catalogHash': digest, 'terminalStarted': started}


def synchronize_once(workers):
    """Scheduler passes only idle slots; copy at most one target per cycle."""
    pair = choose_pair(workers)
    if pair is None:
        return None
    source, target, snapshot, _ = pair
    native, slot = source.windows, source.slot
    with native.inventory_lock(slot['executablePath']):
        pid, identity = native.find_process(slot['executablePath'])
        if not pid or identity != snapshot['processIdentity']:
            raise InventoryError('TERMINAL_CHANGED')
        build = native.terminal_build(slot['executablePath'])
        # Read only servers.dat. A live source is never stopped; reject any
        # rewrite since its positive UI report or during the bounded file read.
        if catalogue_fingerprint(slot['dataPath']) != snapshot['catalogHash']:
            raise InventoryError('CATALOGUE_CHANGED')
        with (Path(slot['dataPath']) / 'config' / 'servers.dat').open('rb') as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        if (sha256(data).hexdigest() != snapshot['catalogHash'] or
                catalogue_fingerprint(slot['dataPath']) != snapshot['catalogHash'] or
                native.process_identity(pid, slot['executablePath']) != identity):
            raise InventoryError('CATALOGUE_CHANGED')
    return install_catalogue(target, data, set(snapshot['serverNames']), build)
