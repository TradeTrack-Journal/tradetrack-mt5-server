"""Recover server-fenced slots without interrupting a collection or replacement PID."""
from .node_agent import InventoryAgent
from .server_preparation import close_terminal, start_terminal
from .windows_inventory import InventoryError, WindowsTerminal
from .update_recovery import terminate_verified


def recover_quarantined(config, token, slot):
    agent = InventoryAgent(config, token)
    native = WindowsTerminal()
    # The collector holds this same lock for claim, native child and completion.
    with native.inventory_lock(slot['executablePath']):
        agent.connect()  # Refresh authoritative quarantine and validate exact paths under lock.
        identity = agent.remote_slots[slot['id']].get('quarantineIdentity')
        if not identity:
            return 'healthy'
        pid, current = native.find_process(slot['executablePath'])
        if pid and current != identity:
            return 'replacement_pending_report'
        # API sets quarantine atomically with lease release; claims are fenced until
        # a different process is reported. Never bypass or clear that server fence.
        if pid:
            try:
                close_terminal(native, slot['executablePath'])
            except InventoryError as exc:
                if str(exc) != 'TERMINAL_STOP_TIMEOUT':
                    raise
                # Graceful close was accepted but did not finish. Revalidate the
                # server fence and pin the exact process handle before forcing it.
                agent.connect()
                if agent.remote_slots[slot['id']].get('quarantineIdentity') != identity:
                    return 'fence_changed'
                if native.find_process(slot['executablePath']) != (pid, identity):
                    return 'replacement_pending_report'
                terminate_verified(native, slot['executablePath'], pid, identity)
        if start_terminal(slot['executablePath']) is False:
            return 'update_pending'
        return 'restarted'
