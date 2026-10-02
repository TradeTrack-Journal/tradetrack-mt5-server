"""Independent quarantine recovery; does not claim jobs or handle credentials."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from app.collector import telemetry
from app.collector.node_agent import AgentError, InventoryAgent, load_config
from app.collector.quarantine_recovery import recover_quarantined
from app.collector.scheduler import error_code
from app.collector.windows_inventory import InventoryError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--log', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = load_config(args.config)
    token = os.environ.pop('MT5_AGENT_TOKEN', '')
    telemetry.initialize(Path(args.config).resolve().parent / 'telemetry-state.sqlite3')
    retry_at = {}

    def emit(result):
        result['at'] = datetime.now(timezone.utc).isoformat()
        # Direct append bypasses Windows PowerShell native-output buffering.
        with Path(args.log).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(result) + '\n')
        telemetry.report(result)

    while True:
        try:
            # A pool expansion must not leave the watchdog comparing against
            # its old slot list forever. Validate the complete file on each pass.
            config = load_config(args.config)
            agent = InventoryAgent(config, token)
            agent.connect()
            try:
                inventory = agent.client.call('/inventory')
                emit({'state': 'health', 'slots': [
                    {k: s.get(k) for k in ('id', 'status', 'lastHeartbeat', 'errorCode')}
                    for s in inventory.get('slots', [])]})
            except AgentError as exc:
                # Detailed status is diagnostic only. Recovery rechecks the
                # authoritative config fence under the slot lock itself.
                emit({'state': 'inventory_unavailable', 'errorCode': error_code(exc)})
            for slot in config['slots']:
                if not agent.remote_slots[slot['id']].get('quarantineIdentity'):
                    continue
                if time.monotonic() < retry_at.get(slot['id'], 0):
                    continue
                try:
                    state = recover_quarantined(config, token, slot)
                    retry_at[slot['id']] = time.monotonic() + (300 if state == 'restarted' else 45)
                    emit({'slotId': slot['id'], 'state': state})
                except (AgentError, InventoryError) as exc:
                    retry_at[slot['id']] = time.monotonic() + 60
                    emit({'slotId': slot['id'], 'state': 'recovery_deferred', 'errorCode': str(exc)})
        except (AgentError, InventoryError) as exc:
            emit({'state': 'health_unavailable', 'errorCode': 'HEALTH_API_UNAVAILABLE', 'cause': error_code(exc)})
        if args.once:
            break
        time.sleep(45)
    telemetry.flush()


if __name__ == '__main__':
    main()
