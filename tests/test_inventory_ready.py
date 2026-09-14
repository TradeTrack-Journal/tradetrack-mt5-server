"""MT5 may publish a dialog HWND before its Server controls are populated."""
from contextlib import nullcontext
import unittest
from unittest.mock import MagicMock, patch

from app.collector.windows_inventory import InventoryError, WindowsTerminal


class InventoryControlReadinessTests(unittest.TestCase):
    def terminal(self):
        windows = object.__new__(WindowsTerminal)
        windows.u = MagicMock()
        windows.inventory_lock = MagicMock(return_value=nullcontext())
        windows.windows = MagicMock(side_effect=[[1], [1, 2]])
        windows.class_name = lambda handle: {1: 'MetaQuotes::MetaTrader::5.00',
                                             2: '#32770', 3: 'Static', 4: 'ComboBox'}[handle]
        windows.process_identity = MagicMock(return_value='12:34')
        windows.login_command = MagicMock(return_value=[100])
        windows.u.GetWindow.return_value = 1
        windows.u.IsWindowEnabled.return_value = True
        windows.u.PostMessageW.return_value = True
        windows.u.IsWindow.side_effect = [True, False, False]
        windows.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', 'Server:')
        windows.send = MagicMock(return_value=0)
        return windows

    def test_waits_for_controls_then_reads_and_cancels(self):
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [0, 0, 3, 4]
        with patch('app.collector.windows_inventory.time.sleep') as sleep:
            self.assertEqual(windows.visible_servers(12, 'terminal64.exe', '12:34'), [])
        sleep.assert_called_with(0.05)
        self.assertEqual(windows.u.GetDlgItem.call_count, 4)
        windows.u.PostMessageW.assert_any_call(2, 0x111, 2, 0)

    def test_unsupported_controls_still_fail_and_cancel(self):
        windows = self.terminal()
        windows.u.GetDlgItem.return_value = 0
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=range(10)):
            with self.assertRaisesRegex(InventoryError, 'UNSUPPORTED_TERMINAL_UI'):
                windows.visible_servers(12, 'terminal64.exe', '12:34')
        windows.send.assert_not_called()
        windows.u.PostMessageW.assert_any_call(2, 0x111, 2, 0)

    def test_slow_cancel_is_awaited_without_submitting_login(self):
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [3, 4]
        clock = [0.0]
        windows.u.IsWindow.side_effect = lambda handle: clock[0] < 1.5
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.collector.windows_inventory.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            self.assertEqual(windows.visible_servers(12, 'terminal64.exe', '12:34'), [])
        self.assertGreaterEqual(clock[0], 1.5)
        self.assertEqual(windows.u.PostMessageW.call_args_list[-1].args, (2, 0x111, 2, 0))

    def test_stuck_cancel_still_fails_closed(self):
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [3, 4]
        windows.u.IsWindow.side_effect = None
        windows.u.IsWindow.return_value = True
        clock = [0.0]
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.collector.windows_inventory.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            with self.assertRaisesRegex(InventoryError, 'UI_CLEANUP_FAILED'):
                windows.visible_servers(12, 'terminal64.exe', '12:34')
        self.assertLess(clock[0], 3.1)
