from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from app.collector.restart_budget import reserve_restart
from app.collector.windows_inventory import InventoryError


class RestartBudgetTests(TestCase):
    def test_staggers_slots_and_survives_caller_restart(self):
        with TemporaryDirectory() as root:
            one = Path(root) / 'one/terminal64.exe'
            two = Path(root) / 'two/terminal64.exe'
            reserve_restart(one, '11:100', 1000)
            reserve_restart(one, '11:100', 1001)  # Same fenced PID is idempotent.
            with self.assertRaisesRegex(InventoryError, 'RECOVERY_COOLDOWN'):
                reserve_restart(two, '22:200', 1119)
            reserve_restart(two, '22:200', 1120)
            with self.assertRaisesRegex(InventoryError, 'RECOVERY_COOLDOWN'):
                reserve_restart(one, '11:101', 1500)  # PID reuse/new generation.
            reserve_restart(one, '11:101', 1900)

    def test_corrupt_state_fails_closed(self):
        with TemporaryDirectory() as root:
            (Path(root) / '.restart-budget.sqlite3').write_bytes(b'corrupt')
            with self.assertRaisesRegex(InventoryError, 'RECOVERY_BUDGET_UNAVAILABLE'):
                reserve_restart(Path(root) / 'one/terminal64.exe', '1:2', 1000)

    def test_clock_rollback_does_not_bypass_cooldown(self):
        with TemporaryDirectory() as root:
            one = Path(root) / 'one/terminal64.exe'
            reserve_restart(one, '1:1', 1000)
            with self.assertRaisesRegex(InventoryError, 'RECOVERY_COOLDOWN'):
                reserve_restart(one, '1:2', 999)
