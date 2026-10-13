"""Recover server-fenced slots without interrupting a collection or replacement PID."""
from .node_agent import InventoryAgent
from .server_preparation import stop_fenced_terminal, start_terminal
from .windows_inventory import InventoryError, WindowsTerminal


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
            stop_fenced_terminal(native, slot['executablePath'], identity, agent.client, slot['id'])
        if start_terminal(slot['executablePath']) is False:
            return 'update_pending'
        return 'restarted'
