"""MT5 may publish a dialog HWND before its Server controls are populated."""
from contextlib import nullcontext
import unittest
from unittest.mock import MagicMock, patch

from app.collector.windows_inventory import InventoryError, WindowsTerminal


class InventoryControlReadinessTests(unittest.TestCase):
    def test_full_posted_queue_does_not_prevent_inventory_or_cancel(self):
        windows = self.terminal()
        windows.u.PostMessageW.return_value = False  # Win32 ERROR_NOT_ENOUGH_QUOTA
        windows.u.GetDlgItem.side_effect = [3, 4]
        self.assertEqual(windows.visible_servers(12, 'terminal64.exe', '12:34'), [])
        windows.u.PostMessageW.assert_not_called()
        self.assertEqual([call.args for call in windows.u.SendNotifyMessageW.call_args_list],
                         [(1, 0x111, 100, 0), (2, 0x111, 2, 0)])

    def test_failed_cancel_cannot_publish_inventory(self):
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [3, 4]
        windows.u.SendNotifyMessageW.side_effect = [True, False]
        with self.assertRaisesRegex(InventoryError, 'UI_CLEANUP_FAILED'):
            windows.visible_servers(12, 'terminal64.exe', '12:34')

    def test_late_managed_login_is_consumed_without_second_menu_command(self):
        windows = self.terminal()
        windows.windows.side_effect = lambda pid: [1, 2]
        windows.class_name = lambda h: {1:'MetaQuotes::MetaTrader::5.00', 2:'#32770',
                                        3:'Static', 4:'ComboBox', 5:'Edit', 6:'Button'}[h]
        windows.u.GetDlgItem.side_effect = lambda h, i: {10405:3,10139:4,10138:5,2:6}[i]
        windows.u.GetWindowTextW.side_effect = lambda h,b,s: setattr(b,'value',{2:'Login',3:'Server:',6:'Cancel'}[h])
        windows.u.IsWindowEnabled.return_value = False
        self.assertEqual(windows.visible_servers(12,'terminal64.exe','12:34',managed_login=True), [])
        windows.login_command.assert_not_called()
        windows.u.SendNotifyMessageW.assert_called_once_with(2, 0x111, 2, 0)

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
        windows.u.SendNotifyMessageW.return_value = True
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
        windows.u.SendNotifyMessageW.assert_any_call(2, 0x111, 2, 0)

    def test_dialog_delayed_by_cpu_contention_is_still_read_and_cancelled(self):
        windows = self.terminal()
        clock = [0.0]
        windows.windows.side_effect = lambda pid: [1, 2] if clock[0] >= 4 else [1]
        windows.u.GetDlgItem.side_effect = [3, 4]
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.collector.windows_inventory.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            self.assertEqual(windows.visible_servers(12, 'terminal64.exe', '12:34'), [])
        self.assertGreaterEqual(clock[0], 4)
        self.assertLess(clock[0], 10)
        windows.u.SendNotifyMessageW.assert_any_call(2, 0x111, 2, 0)

    def test_unsupported_controls_still_fail_and_cancel(self):
        windows = self.terminal()
        windows.u.GetDlgItem.return_value = 0
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=range(10)):
            with self.assertRaisesRegex(InventoryError, 'UNSUPPORTED_TERMINAL_UI'):
                windows.visible_servers(12, 'terminal64.exe', '12:34')
        windows.send.assert_not_called()
        windows.u.SendNotifyMessageW.assert_any_call(2, 0x111, 2, 0)

    def test_large_catalogue_with_slow_valid_replies_is_not_false_timeout(self):
        import ctypes
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [3, 4]
        clock = [0.0]
        def send(handle, message, index=0, buffer=0):
            clock[0] += 0.012
            if message == 0x146:
                return 459
            name = f'Broker-Server{index}'
            if message == 0x148:
                source = ctypes.create_unicode_buffer(name)
                ctypes.memmove(buffer, source, ctypes.sizeof(source))
            return len(name)
        windows.send.side_effect = send
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(len(windows.visible_servers(12, 'terminal64.exe', '12:34')), 459)
        self.assertGreater(clock[0], 8)
        self.assertLess(clock[0], 20)

    def test_slow_cancel_is_awaited_without_submitting_login(self):
        windows = self.terminal()
        windows.u.GetDlgItem.side_effect = [3, 4]
        clock = [0.0]
        windows.u.IsWindow.side_effect = lambda handle: clock[0] < 1.5
        with patch('app.collector.windows_inventory.time.monotonic', side_effect=lambda: clock[0]), \
                patch('app.collector.windows_inventory.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            self.assertEqual(windows.visible_servers(12, 'terminal64.exe', '12:34'), [])
        self.assertGreaterEqual(clock[0], 1.5)
        self.assertEqual(windows.u.SendNotifyMessageW.call_args_list[-1].args, (2, 0x111, 2, 0))

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
