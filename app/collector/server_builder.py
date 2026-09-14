"""Credential-free discovery in a dedicated builder, never in collection slots."""
import ctypes as c
from pathlib import Path
import time

from .windows_inventory import WindowsTerminal, InventoryError, _inventory_ui_lock


def validate_builder(executable, company, server):
    path = Path(executable).resolve()
    if path.name.lower() != 'terminal64.exe' or path.parent.name != 'server-builder':
        raise InventoryError('DEDICATED_BUILDER_REQUIRED')
    for value in (company, server):
        if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(x) < 32 for x in value):
            raise InventoryError('INVALID_DISCOVERY_REQUEST')
    return path


class ServerBuilder(WindowsTerminal):
    def text(self, hwnd):
        buffer = c.create_unicode_buffer(512)
        self.u.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.replace('&', '').strip()

    def commands(self, menu, depth=0):
        if depth > 5:
            raise InventoryError('UNSUPPORTED_TERMINAL_UI')
        found = []
        for index in range(max(0, self.u.GetMenuItemCount(menu))):
            buffer = c.create_unicode_buffer(256)
            self.u.GetMenuStringW(menu, index, buffer, len(buffer), 0x400)
            label = buffer.value.replace('&', '').split('\t')[0].rstrip('. ').strip()
            if label == 'Open an Account':
                found.append(self.u.GetMenuItemID(menu, index))
            child = self.u.GetSubMenu(menu, index)
            if child:
                found.extend(self.commands(child, depth + 1))
        return found

    def children(self, parent):
        result = []
        callback_type = c.WINFUNCTYPE(c.c_bool, c.c_void_p, c.c_ssize_t)
        callback = callback_type(lambda hwnd, _: result.append(hwnd) is None)
        self.u.EnumChildWindows.argtypes = [c.c_void_p, callback_type, c.c_ssize_t]
        self.u.EnumChildWindows(parent, callback, 0)
        return result

    def search(self, executable, company, server):
        path = validate_builder(executable, company, server)
        pid, identity = self.find_process(str(path))
        if not pid or not identity:
            raise InventoryError('BUILDER_NOT_RUNNING')
        # Reuse an already prepared catalog before opening the company wizard.
        # Names still come from the live terminal, never from the central DB.
        try:
            known = self.visible_servers(pid, str(path), identity)
            if server in known:
                return {'serverName': server, 'state': 'PRESENT', 'verificationMethod': 'login_dialog', 'serverNames': known}
        except InventoryError as exc:
            if str(exc) != 'TERMINAL_UI_BUSY':
                raise
        with _inventory_ui_lock, self.inventory_lock(str(path)):
            before = [h for h in self.windows(pid) if self.class_name(h) != '#32770' or self.u.IsWindowVisible(h)]
            mains = [h for h in before if self.class_name(h) == 'MetaQuotes::MetaTrader::5.00']
            initial_dialogs = [h for h in before if self.class_name(h) == '#32770']
            updates = [h for h in initial_dialogs if self.text(h) == 'Welcome to LiveUpdate']
            if len(mains) == 1 and len(updates) == 1 and len(initial_dialogs) <= 2:
                owner = self.u.GetWindow(updates[0], 4)
                if owner != mains[0] and not (owner in initial_dialogs and self.text(owner) == 'Open an Account'):
                    raise InventoryError('TERMINAL_UI_BUSY')
                self.defer_live_update(pid, str(path), identity, owner, updates[0])
                before = [h for h in self.windows(pid) if self.class_name(h) != '#32770' or self.u.IsWindowVisible(h)]
                initial_dialogs = [h for h in before if self.class_name(h) == '#32770']
            for _ in range(3):
                if len(mains) != 1 or len(initial_dialogs) <= 1:
                    break
                if not all(self.text(h) == 'Open an Account' for h in initial_dialogs):
                    break
                active = [h for h in initial_dialogs if self.u.IsWindowEnabled(h)]
                if len(active) != 1:
                    break
                owner = self.u.GetWindow(active[0], 4)
                if owner != mains[0] and owner not in initial_dialogs:
                    break
                self.defer_live_update(pid, str(path), identity, owner, active[0])
                before = [h for h in self.windows(pid) if self.class_name(h) != '#32770' or self.u.IsWindowVisible(h)]
                initial_dialogs = [h for h in before if self.class_name(h) == '#32770']
            if len(mains) != 1 or len(initial_dialogs) > 1 or (initial_dialogs and self.text(initial_dialogs[0]) != 'Open an Account'):
                raise InventoryError(f'DISCOVERY_UI_BUSY_{len(mains)}_{len(initial_dialogs)}')
            commands = self.commands(self.u.GetMenu(mains[0]))
            if len(commands) != 1:
                raise InventoryError('DISCOVERY_COMMAND_UNAVAILABLE')
            if not initial_dialogs:
                self.u.PostMessageW(mains[0], 0x111, commands[0], 0)
            dialog = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    dialogs = initial_dialogs or [h for h in self.windows(pid) if h not in before and self.class_name(h) == '#32770']
                    if len(dialogs) == 1:
                        dialog = dialogs[0]
                        children = self.children(dialog)
                        edits = [h for h in children if self.class_name(h) == 'Edit' and self.u.IsWindowVisible(h)]
                        buttons = [h for h in children if self.class_name(h) == 'Button' and self.text(h) == 'Find your company']
                        if len(edits) == len(buttons) == 1:
                            break
                    time.sleep(0.1)
                else:
                    raise InventoryError('DISCOVERY_CONTROLS_UNAVAILABLE')
                if self.process_identity(pid, str(path)) != identity:
                    raise InventoryError('TERMINAL_CHANGED')
                text = c.create_unicode_buffer(company)
                self.send(edits[0], 0xC, 0, c.cast(text, c.c_void_p).value)
                self.send(buttons[0], 0xF5)  # BM_CLICK: search only, never Next or account creation.
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if self.process_identity(pid, str(path)) != identity:
                        raise InventoryError('TERMINAL_CHANGED')
                    if not self.text(edits[0]) and self.u.IsWindowEnabled(buttons[0]):
                        break
                    time.sleep(0.2)
                else:
                    raise InventoryError('DISCOVERY_TIMEOUT')
            finally:
                if dialog and self.u.IsWindow(dialog):
                    cancel = self.u.GetDlgItem(dialog, 2)
                    if not cancel or self.class_name(cancel) != 'Button' or self.text(cancel) != 'Cancel':
                        raise InventoryError('DISCOVERY_CANCEL_UNAVAILABLE')
                    self.send(cancel, 0xF5)
                    deadline = time.monotonic() + 3
                    while self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog):
                        raise InventoryError('UI_CLEANUP_FAILED')
        names = self.visible_servers(pid, str(path), identity)
        return {'serverName': server, 'state': 'PRESENT' if server in names else 'SERVER_NOT_FOUND', 'verificationMethod': 'login_dialog', 'serverNames': names}
