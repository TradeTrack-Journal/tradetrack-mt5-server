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
from app.collector.diagnostics import error_fields
from app.collector.windows_inventory import InventoryError, WindowsTerminal
from app.collector.ui_recovery import UiRecoveryBudget, fence_ui_failure


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
    ui_budget = UiRecoveryBudget(Path(args.config).resolve().parent / 'ui-recovery-budget.json')

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
                emit({'state': 'inventory_unavailable', 'errorCode': error_code(exc), **error_fields(exc)})
            for slot in config['slots']:
                observed = agent.remote_slots[slot['id']]
                if ui_budget.observe(dict(observed, id=slot['id']), time.time()):
                    try:
                        if ui_budget.reserve(slot['id'], time.time()) and fence_ui_failure(agent, slot, observed):
                            emit({'slotId': slot['id'], 'state': 'ui_recovery_fenced',
                                  'cause': observed.get('inventoryErrorCode')})
                            agent.connect()
                    except (AgentError, InventoryError, OSError, ValueError, TypeError, KeyError) as exc:
                        emit({'slotId': slot['id'], 'state': 'ui_recovery_deferred',
                              'errorCode': error_code(exc), **error_fields(exc)})
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
                    emit({'slotId': slot['id'], 'state': 'recovery_deferred', 'errorCode': error_code(exc), **error_fields(exc)})
        except (AgentError, InventoryError) as exc:
            emit({'state': 'health_unavailable', 'errorCode': 'HEALTH_API_UNAVAILABLE', 'cause': error_code(exc), **error_fields(exc)})
        if args.once:
            break
        time.sleep(45)
    telemetry.flush()


def run_single_instance():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--config')
    options, _ = parser.parse_known_args()
    if not options.config:
        return main()  # Let the full parser handle --help or invalid arguments.
    # Scheduled Task termination can leave Python alive after its PowerShell
    # parent exits. Keep a second mutex owned by the process doing recovery.
    lock_key = str(Path(options.config).resolve()) + '.watchdog'
    with WindowsTerminal().inventory_lock(lock_key):
        main()


if __name__ == '__main__':
    run_single_instance()
