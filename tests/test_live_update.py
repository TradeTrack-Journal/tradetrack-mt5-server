import unittest
from unittest.mock import MagicMock

from app.collector.windows_inventory import WindowsTerminal, InventoryError


class LiveUpdateTests(unittest.TestCase):
    def terminal(self, title='Welcome to LiveUpdate', identity='verified'):
        native = WindowsTerminal.__new__(WindowsTerminal)
        native.u = MagicMock()
        native.u.GetDlgItem.side_effect = lambda dialog, control: control + 10
        native.u.GetWindow.return_value = 100
        labels = {200: title, 12: 'Later', 11: 'Restart'}
        native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', labels[handle])
        native.u.IsWindow.return_value = False
        native.class_name = MagicMock(return_value='Button')
        native.process_identity = MagicMock(return_value=identity)
        return native

    def test_defers_without_restart(self):
        native = self.terminal()
        native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)

    def test_unrelated_dialog_is_never_dismissed(self):
        native = self.terminal(title='Login')
        with self.assertRaisesRegex(InventoryError, 'TERMINAL_UI_BUSY'):
            native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_not_called()

    def test_hidden_retained_dialog_counts_as_closed(self):
        native = self.terminal()
        native.u.IsWindow.return_value = True
        native.u.IsWindowVisible.return_value = False
        native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)

    def test_known_hidden_owner_chain_is_verified(self):
        native = self.terminal()
        labels = {200:'Welcome to LiveUpdate', 12:'Later', 11:'Restart', 300:'Login'}
        native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', labels[handle])
        native.u.GetWindow.side_effect = lambda handle, flag: 300 if handle == 200 else 100
        native.windows = MagicMock(return_value=[100,200,300])
        native.class_name.side_effect = lambda handle: '#32770' if handle == 300 else 'Button'
        native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)

    def test_foreign_owner_is_not_trusted(self):
        native = self.terminal()
        native.u.GetWindow.return_value = 300
        native.windows = MagicMock(return_value=[100,200])
        with self.assertRaisesRegex(InventoryError, 'TERMINAL_UI_BUSY'):
            native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_not_called()

    def test_owner_cycle_is_bounded(self):
        native = self.terminal()
        native.u.GetWindow.return_value = 300
        native.windows = MagicMock(return_value=[100,200,300])
        native.class_name.return_value = '#32770'
        native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', 'Login')
        self.assertFalse(native.owned_by_main(1, 100, 200))
        self.assertLessEqual(native.u.GetWindow.call_count, 5)

    def test_changed_process_is_never_touched(self):
        native = self.terminal(identity='replaced')
        with self.assertRaisesRegex(InventoryError, 'TERMINAL_CHANGED'):
            native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_not_called()

    def test_startup_wizard_is_cancelled_without_creating_account(self):
        native = self.terminal(title='Open an Account')
        labels = {200: 'Open an Account', 12: 'Cancel', 12334: 'Next >'}
        native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', labels[handle])
        native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
        native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)

    def test_blank_startup_login_is_cancelled_but_typed_password_is_preserved(self):
        for length in (0, 8):
            native = self.terminal(title='Login')
            labels = {200:'Login', 12:'Cancel', 10415:'Server:'}
            native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', labels[handle])
            native.class_name.side_effect = lambda handle: {10148:'Edit', 10149:'ComboBox'}.get(handle, 'Button')
            native.send = MagicMock(return_value=length)
            if length:
                with self.assertRaisesRegex(InventoryError, 'TERMINAL_UI_BUSY'):
                    native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
                native.u.PostMessageW.assert_not_called()
            else:
                native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200)
                native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)

    def test_managed_slot_cancels_stale_login_without_reading_password(self):
        native = self.terminal(title='Login')
        labels = {200:'Login', 12:'Cancel', 10415:'Server:'}
        native.u.GetWindowTextW.side_effect = lambda handle, buffer, size: setattr(buffer, 'value', labels[handle])
        native.class_name.side_effect = lambda handle: {10148:'Edit', 10149:'ComboBox'}.get(handle, 'Button')
        native.send = MagicMock(side_effect=AssertionError('Password must not be read'))
        native.defer_live_update(1, 'terminal64.exe', 'verified', 100, 200, managed_login=True)
        native.u.PostMessageW.assert_called_once_with(200, 0x111, 2, 12)
