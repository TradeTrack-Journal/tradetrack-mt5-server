"""Read-only weekly MT5 maintenance report. This is NOT an update installer.

Uses the worker's existing DPAPI-protected node token for GET requests only.
Never opens account credentials, starts/stops terminals, approves builds, or
invokes LiveUpdate. Package filename versions are hints, not trusted binaries.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from app.collector.node_agent import InventoryAgent, load_config
from app.collector.terminal_build import VERIFIED_BUILDS, file_build
from scripts.supervise_production import load_token


def heartbeat_fresh(value, now):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return False
        return -5 <= (now - stamp).total_seconds() <= 120
    except (TypeError, ValueError, AttributeError):
        return False


def summarize(config, remote, inventory, builds, accepted_builds, now):
    configured = {slot['id'] for slot in config['slots']}
    observed = inventory.get('slots', [])
    ids = [slot.get('id') for slot in observed]
    exact_inventory = len(ids) == len(configured) and set(ids) == configured
    by_id = {slot.get('id'): slot for slot in observed}
    rows = []
    for slot_id in sorted(configured):
        current = by_id.get(slot_id, {})
        fence = remote.get(slot_id, {}).get('quarantineIdentity')
        build = builds.get(slot_id)
        supported = build in VERIFIED_BUILDS and build in accepted_builds
        fresh = heartbeat_fresh(current.get('lastHeartbeat'), now)
        healthy = (exact_inventory and supported and fresh and not fence
                   and current.get('status') in ('STARTING', 'BUSY')
                   and not current.get('errorCode'))
        rows.append(dict(slotId=slot_id, status=current.get('status', 'UNKNOWN'),
                         freshHeartbeat=fresh, quarantined=bool(fence),
                         installedBuild=build, supportedBuild=supported, healthy=healthy))
    return dict(at=now.isoformat(), state='maintenance_preflight', readOnly=True,
                healthySlots=sum(row['healthy'] for row in rows), totalSlots=len(rows),
                inventoryMatches=exact_inventory, slots=rows,
                automaticInstallationEnabled=False,
                installationBlocker='UNATTENDED_INSTALLER_NOT_VALIDATED')


def pending_packages(config, root):
    """Inspect only active per-slot staging; archived failed packages aren't new updates."""
    slots = {Path(s['executablePath']).resolve().parent: s['id'] for s in config['slots']}
    result = []
    for origin in root.glob('*/origin.txt'):
        if origin.is_symlink() or origin.parent.is_symlink() or origin.parent.is_junction():
            raise ValueError('UNSAFE_UPDATE_PATH')
        with origin.open('rb') as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError('ORIGIN_TOO_LARGE')
        value = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
        slot_id = slots.get(Path(value.strip()).resolve())
        if not slot_id:
            continue
        folder = origin.parent / 'liveupdate'
        if folder.is_symlink() or folder.is_junction():
            raise ValueError('UNSAFE_UPDATE_PATH')
        files = list(folder.iterdir()) if folder.exists() else []
        if len(files) > 256:
            raise ValueError('UPDATE_FILE_LIMIT')
        if any(p.is_symlink() or p.is_junction() for p in files):
            raise ValueError('UNSAFE_UPDATE_PATH')
        hints = sorted({int(m.group(1)) for p in files
                        if (m := re.fullmatch(r'mt5[a-z0-9]+\.(\d{4,6})', p.name))})
        if files:
            result.append(dict(slotId=slot_id, fileCount=len(files), candidateBuildHints=hints))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-directory', required=True)
    args = parser.parse_args()
    try:
        directory = Path(args.config_directory).resolve()
        config = load_config(directory / 'agent.json')
        agent = InventoryAgent(config, load_token(directory / 'node-token.dpapi'))
        agent.connect()  # GET /config; verifies the exact registered slot paths.
        inventory = agent.client.call('/inventory')  # GET only, no lease or account calls.
        builds = {s['id']: file_build(s['executablePath']) for s in config['slots']}
        result = summarize(config, agent.remote_slots, inventory, builds,
                           agent.accepted_builds, datetime.now(timezone.utc))
        root = Path(os.environ['APPDATA']) / 'MetaQuotes' / 'Terminal'
        result['pendingPackages'] = pending_packages(config, root)
        print(json.dumps(result))
        return 0 if result['healthySlots'] == result['totalSlots'] else 2
    except Exception:
        # Exceptions may contain paths or response details. Never dump them or secrets.
        print(json.dumps(dict(state='maintenance_preflight_unavailable', readOnly=True,
                              automaticInstallationEnabled=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
