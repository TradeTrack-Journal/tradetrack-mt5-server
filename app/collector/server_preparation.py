"""Between-job server preparation; slot replacement is fenced and offline only."""
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import time

from .server_builder import ServerBuilder, validate_builder
from .windows_inventory import InventoryError, catalogue_fingerprint


def start_terminal(executable):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.Popen([str(executable), '/portable'], cwd=str(Path(executable).parent), startupinfo=startup)


def close_terminal(native, executable):
    pid, identity = native.find_process(str(executable))
    if not pid:
        return
    mains = [h for h in native.windows(pid) if native.class_name(h) == 'MetaQuotes::MetaTrader::5.00']
    dialogs = [h for h in native.windows(pid) if native.class_name(h) == '#32770' and native.u.IsWindowVisible(h)]
    if len(mains) == 1 and len(dialogs) == 1:
        native.defer_live_update(pid, str(executable), identity, mains[0], dialogs[0])
    if len(mains) != 1 or any(native.class_name(h) == '#32770' and native.u.IsWindowVisible(h) for h in native.windows(pid)):
        raise InventoryError('TERMINAL_UI_BUSY')
    if native.process_identity(pid, str(executable)) != identity:
        raise InventoryError('TERMINAL_CHANGED')
    native.u.PostMessageW(mains[0], 0x10, 0, 0)
    deadline = time.monotonic() + 10
    while native.find_process(str(executable))[0] and time.monotonic() < deadline:
        time.sleep(0.1)
    if native.find_process(str(executable))[0]:
        raise InventoryError('TERMINAL_STOP_TIMEOUT')


class ServerPreparation:
    def __init__(self, builder):
        self.builder = Path(builder).resolve()
        validate_builder(self.builder, 'validation', 'validation')
        self.native = ServerBuilder()
        self.retry_at = {}

    def once(self, workers):
        if not workers:
            return None
        requests = workers[0].client.call('/config').get('preparationRequests', [])
        for request in requests[:100]:
            server, company = request.get('serverName'), request.get('company')
            validate_builder(self.builder, company, server)
            missing = [w for w in workers if server not in w.inventory.last_snapshots.get(w.slot['id'], {}).get('serverNames', [])]
            if not missing or time.monotonic() < self.retry_at.get(server, 0):
                continue
            if not self.native.find_process(str(self.builder))[0]:
                start_terminal(self.builder)
                return {'state': 'BUILDER_STARTING', 'serverName': server}
            self.retry_at[server] = time.monotonic() + 15
            try:
                result = self.native.search(str(self.builder), company, server)
            except InventoryError as exc:
                if str(exc) == 'DISCOVERY_UI_BUSY_0_0':
                    self.retry_at.pop(server, None)
                    return {'state': 'BUILDER_STARTING', 'serverName': server}
                raise
            self.retry_at[server] = time.monotonic() + 300
            if result['state'] != 'PRESENT':
                workers[0].client.call('/preparation-missing', {'serverName': server})
                return {'state': result['state'], 'serverName': server}
            # Preserve every observed name, including manually added slot servers.
            names = set(result['serverNames'])
            eligible = [w for w in missing if w.slot['id'] in w.inventory.sessions and set(w.inventory.last_snapshots.get(w.slot['id'], {}).get('serverNames', [])).issubset(names)]
            if not eligible:
                raise InventoryError('BUILDER_CATALOG_INCOMPLETE')
            with self.native.inventory_lock(str(self.builder)):
                close_terminal(self.native, self.builder)
                source = self.builder.parent / 'config' / 'servers.dat'
                data = source.read_bytes()
                digest = sha256(data).hexdigest()
                artifact = self.builder.parent.parent / 'server-catalogs' / digest
                artifact.mkdir(parents=True, exist_ok=True)
                frozen = artifact / 'servers.dat'
                if frozen.exists() and frozen.read_bytes() != data:
                    raise InventoryError('ARTIFACT_HASH_MISMATCH')
                if not frozen.exists():
                    frozen.write_bytes(data)
            for worker in eligible:
                executable = Path(worker.slot['executablePath']).resolve()
                target_root = Path(worker.slot['dataPath']).resolve()
                if target_root != executable.parent or self.builder.parent == target_root:
                    raise InventoryError('INVALID_SLOT_PATH')
                marker = target_root / '.server-preparing'
                with self.native.inventory_lock(str(executable)):
                    session = worker.inventory.sessions[worker.slot['id']]
                    if self.native.find_process(str(executable))[1] != session['processIdentity']:
                        raise InventoryError('TERMINAL_CHANGED')
                    if catalogue_fingerprint(str(target_root)) != worker.catalog_hash:
                        raise InventoryError('TERMINAL_CHANGED')
                    with marker.open('x'):
                        pass
                    try:
                        worker.client.call(f"/slots/{worker.slot['id']}/prepare", {'generation': session['generation']})
                        close_terminal(self.native, executable)
                        if self.native.find_process(str(executable))[0]:
                            raise InventoryError('SLOT_MUST_BE_STOPPED')
                        target = target_root / 'config' / 'servers.dat'
                        backup = artifact / (worker.slot['id'] + '-' + str(time.time_ns()) + '.previous.servers.dat')
                        shutil.copy2(target, backup)
                        temporary = target.with_name('servers.dat.preparing')
                        with temporary.open('xb') as output:
                            output.write(data)
                        if sha256(temporary.read_bytes()).hexdigest() != digest or self.native.find_process(str(executable))[0]:
                            raise InventoryError('CATALOG_INSTALL_CONFLICT')
                        os.replace(temporary, target)
                        start_terminal(executable)
                        worker.inspected_at = 0
                    finally:
                        marker.unlink(missing_ok=True)
            return {'state': 'AWAITING_FRESH_INVENTORY', 'serverName': server, 'slots': len(eligible), 'catalogHash': digest}
        return None
