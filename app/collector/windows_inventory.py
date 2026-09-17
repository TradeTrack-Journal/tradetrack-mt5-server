"""Read-only evidence from an explicitly configured, already running MT5.

No native initialize/login call, terminal launch, binary catalogue parsing or
credential control reads. UI inspection is an explicit maintenance operation.
"""

import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
from hashlib import sha256
import os
import re
from pathlib import Path
import time
from threading import Lock


# MT5 modal inventory in two worker threads can stall the Windows message
# queues. Serialize only this brief UI step; collection remains per-slot.
_inventory_ui_lock = Lock()


class InventoryError(Exception):
    pass


def read_only_caption_matches(caption, login, server, build):
    """Observed English demo and broker read-only formats; other builds fail closed.

    Match the mode's structural position, not a 'Read Only' word in a company name.
    This is an explicit terminal UI signal, not a cryptographic password attestation.
    """
    if type(build) is not int or build not in (6182, 6190, 6193):
        return False
    prefix = f"{login} - {server}: "
    mode = r"(Hedge|Netting)" if build == 6182 else "Hedge"
    return caption.startswith(prefix) and re.fullmatch(
        rf"(?:Demo Account - )?Read Only - {mode} - .+", caption[len(prefix):]
    ) is not None


def catalogue_fingerprint(data_path):
    path = Path(data_path) / "Config" / "servers.dat"
    try:
        before = path.stat()
        if not 0 < before.st_size <= 8 * 1024 * 1024:
            raise InventoryError("CATALOGUE_SIZE")
        with path.open("rb") as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        after = path.stat()
    except OSError:
        raise InventoryError("CATALOGUE_UNAVAILABLE") from None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
        raise InventoryError("CATALOGUE_CHANGED")
    return sha256(data).hexdigest()


def validate_data_path(executable, data_path):
    executable, data_path = Path(executable).resolve(), Path(data_path).resolve()
    if executable.name.lower() != "terminal64.exe" or not executable.is_file():
        raise InventoryError("TERMINAL_PATH_INVALID")
    if data_path == executable.parent:
        return
    try:
        origin = data_path / "origin.txt"
        if origin.stat().st_size > 4096:
            raise InventoryError("DATA_PATH_MISMATCH")
        raw = origin.read_bytes()
        source = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8-sig")
        if Path(source.strip()).resolve() != executable.parent:
            raise InventoryError("DATA_PATH_MISMATCH")
    except (OSError, UnicodeError, ValueError):
        raise InventoryError("DATA_PATH_MISMATCH") from None


class WindowsTerminal:
    def __init__(self):
        if os.name != "nt":
            raise InventoryError("WINDOWS_REQUIRED")
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.u = c.WinDLL("user32", use_last_error=True)
        self.p = c.WinDLL("psapi", use_last_error=True)
        self.callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
        self.k.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        self.k.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
        self.k.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
        self.k.CreateMutexW.restype = w.HANDLE
        self.k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.k.ReleaseMutex.argtypes = [w.HANDLE]
        self.p.EnumProcesses.argtypes = [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)]
        self.u.EnumWindows.argtypes = [self.callback, w.LPARAM]
        self.u.EnumChildWindows.argtypes = [w.HWND, self.callback, w.LPARAM]
        self.u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        self.u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        self.u.GetDlgItem.argtypes = [w.HWND, c.c_int]
        self.u.GetDlgItem.restype = w.HWND
        self.u.GetWindow.argtypes = [w.HWND, w.UINT]
        self.u.GetWindow.restype = w.HWND
        self.u.GetMenu.argtypes = [w.HWND]
        self.u.GetMenu.restype = w.HMENU
        self.u.GetSubMenu.argtypes = [w.HMENU, c.c_int]
        self.u.GetSubMenu.restype = w.HMENU
        self.u.GetMenuItemCount.argtypes = [w.HMENU]
        self.u.GetMenuItemID.argtypes = [w.HMENU, c.c_int]
        self.u.GetMenuItemID.restype = w.UINT
        self.u.GetMenuStringW.argtypes = [w.HMENU, w.UINT, w.LPWSTR, c.c_int, w.UINT]
        self.u.IsWindowEnabled.argtypes = [w.HWND]
        self.u.IsWindow.argtypes = [w.HWND]
        self.u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)]
        self.u.SendMessageTimeoutW.restype = w.LPARAM

    def process_identity(self, pid, expected_path):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            buffer = c.create_unicode_buffer(32768)
            size, status = w.DWORD(len(buffer)), w.DWORD()
            if not self.k.GetExitCodeProcess(handle, c.byref(status)) or status.value != 259:
                return None
            if not self.k.QueryFullProcessImageNameW(handle, 0, buffer, c.byref(size)):
                return None
            if Path(buffer.value).resolve() != Path(expected_path).resolve():
                return None
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(handle, c.byref(created), c.byref(exited), c.byref(kernel), c.byref(user)):
                return None
            return f"{pid}:{created.dwHighDateTime << 32 | created.dwLowDateTime}"
        finally:
            self.k.CloseHandle(handle)

    def find_process(self, expected_path):
        processes, used = (w.DWORD * 16384)(), w.DWORD()
        if not self.p.EnumProcesses(processes, c.sizeof(processes), c.byref(used)) or used.value >= c.sizeof(processes):
            raise InventoryError("PROCESS_ENUMERATION_FAILED")
        matches = []
        for pid in processes[:used.value // c.sizeof(w.DWORD)]:
            identity = self.process_identity(pid, expected_path)
            if identity:
                matches.append((pid, identity))
        if len(matches) > 1:
            raise InventoryError("AMBIGUOUS_TERMINAL_PROCESS")
        return matches[0] if matches else (None, None)

    def class_name(self, hwnd):
        buffer = c.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def read_only_session(self, pid, executable, identity, login, server, build):
        if self.process_identity(pid, executable) != identity:
            return False
        mains = [h for h in self.windows(pid) if self.class_name(h) == "MetaQuotes::MetaTrader::5.00"]
        if len(mains) != 1:
            return False
        buffer = c.create_unicode_buffer(1024)
        count = self.u.GetWindowTextW(mains[0], buffer, len(buffer))
        return (0 < count < len(buffer) - 1 and
                read_only_caption_matches(buffer.value, login, server, build) and
                self.process_identity(pid, executable) == identity)

    def windows(self, pid):
        result = []
        def visit(hwnd, _):
            owner = w.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if owner.value == pid:
                result.append(hwnd)
            return True
        self.u.EnumWindows(self.callback(visit), 0)
        return result

    def send(self, hwnd, message, index=0, pointer=0):
        result = c.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, message, index, pointer, 2, 3000, c.byref(result)):
            raise InventoryError("UI_TIMEOUT")
        return c.c_ssize_t(result.value).value

    def login_command(self, menu, depth=0):
        if depth > 5:
            raise InventoryError("UNSUPPORTED_TERMINAL_UI")
        found = []
        for index in range(max(0, self.u.GetMenuItemCount(menu))):
            label = c.create_unicode_buffer(256)
            self.u.GetMenuStringW(menu, index, label, len(label), 0x400)
            if label.value.replace("&", "").strip() == "Login to Trade Account":
                found.append(self.u.GetMenuItemID(menu, index))
            child = self.u.GetSubMenu(menu, index)
            if child:
                found.extend(self.login_command(child, depth + 1))
        return found

    @contextmanager
    def inventory_lock(self, executable):
        key = sha256(str(Path(executable).resolve()).casefold().encode()).hexdigest()
        handle = self.k.CreateMutexW(None, False, f"Local\\TradeTrack-MT5-{key}")
        if not handle:
            raise InventoryError("SLOT_LOCK_FAILED")
        acquired = False
        try:
            acquired = self.k.WaitForSingleObject(handle, 0) in (0, 0x80)
            if not acquired:
                raise InventoryError("SLOT_BUSY")
            yield
        finally:
            if acquired:
                self.k.ReleaseMutex(handle)
            self.k.CloseHandle(handle)

    def owned_by_main(self, pid, main, dialog):
        owner = self.u.GetWindow(dialog, 4)
        for _ in range(4):
            if owner == main:
                return True
            if owner not in self.windows(pid) or self.class_name(owner) != '#32770':
                return False
            title = c.create_unicode_buffer(256)
            self.u.GetWindowTextW(owner, title, len(title))
            if title.value not in {'Login', 'Open an Account', 'Welcome to LiveUpdate'}:
                return False
            owner = self.u.GetWindow(owner, 4)
        return False

    def defer_live_update(self, pid, executable, identity, main, dialog, managed_login=False):
        """Defer only the exact supported update prompt, under the slot mutex."""
        def text(handle):
            buffer = c.create_unicode_buffer(256)
            self.u.GetWindowTextW(handle, buffer, len(buffer))
            return buffer.value.replace('&', '').strip()

        later = self.u.GetDlgItem(dialog, 2)
        title = text(dialog)
        supported = False
        if title == 'Welcome to LiveUpdate':
            restart = self.u.GetDlgItem(dialog, 1)
            supported = bool(restart and self.class_name(restart) == 'Button'
                             and text(restart) == 'Restart' and later and text(later) == 'Later')
        elif title == 'Open an Account':
            next_button = self.u.GetDlgItem(dialog, 12324)
            supported = bool(next_button and self.class_name(next_button) == 'Button'
                             and text(next_button) == 'Next >' and later and text(later) == 'Cancel')
        elif title == 'Login':
            password = self.u.GetDlgItem(dialog, 10138)
            server = self.u.GetDlgItem(dialog, 10139)
            label = self.u.GetDlgItem(dialog, 10405)
            # Managed terminals intentionally do not persist passwords. On
            # restart MT5 may show this blank prompt before native API login.
            # Interactive tools preserve typed prompts. Dedicated worker slots may
            # cancel stale Login dialogs between jobs; never submit their contents.
            supported = bool(password and server and label and self.class_name(password) == 'Edit'
                             and self.class_name(server) == 'ComboBox' and text(label) == 'Server:'
                             and (managed_login or self.send(password, 0xE) == 0) and later and text(later) == 'Cancel')
        if (not self.owned_by_main(pid, main, dialog) or not supported or not later or self.class_name(later) != 'Button'):
            raise InventoryError('TERMINAL_UI_BUSY')
        if self.process_identity(pid, executable) != identity:
            raise InventoryError('TERMINAL_CHANGED')
        self.u.PostMessageW(dialog, 0x111, 2, later)
        deadline = time.monotonic() + 3
        while self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog) and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog):
            raise InventoryError('UI_CLEANUP_FAILED')

    def visible_servers(self, pid, executable, identity, managed_login=False):
        """Open only our own login dialog, read only its Server combo, cancel it."""
        with _inventory_ui_lock, self.inventory_lock(executable):
            before = [h for h in self.windows(pid) if self.class_name(h) != '#32770' or self.u.IsWindowVisible(h)]
            mains = [h for h in before if self.class_name(h) == "MetaQuotes::MetaTrader::5.00"]
            dialogs = [h for h in before if self.class_name(h) == '#32770']
            if len(mains) == 1 and len(dialogs) == 1:
                self.defer_live_update(pid, executable, identity, mains[0], dialogs[0], managed_login=managed_login)
                before = [h for h in self.windows(pid) if self.class_name(h) != '#32770' or self.u.IsWindowVisible(h)]
            if len(mains) != 1 or any(self.class_name(h) == "#32770" for h in before):
                raise InventoryError("TERMINAL_UI_BUSY")
            main = mains[0]
            if not self.u.IsWindowEnabled(main) or self.process_identity(pid, executable) != identity:
                raise InventoryError("TERMINAL_UI_BUSY")
            commands = self.login_command(self.u.GetMenu(main))
            if len(commands) != 1:
                raise InventoryError("UNSUPPORTED_TERMINAL_UI")
            dialog = None
            if not self.u.PostMessageW(main, 0x111, commands[0], 0):
                raise InventoryError("UI_COMMAND_FAILED")
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    dialogs = [h for h in self.windows(pid) if h not in before and self.class_name(h) == "#32770" and self.u.IsWindowVisible(h) and self.owned_by_main(pid, main, h)]
                    if len(dialogs) == 1:
                        dialog = dialogs[0]
                        break
                    time.sleep(0.05)
                if dialog is None:
                    raise InventoryError("UI_DIALOG_UNAVAILABLE")
                # The dialog HWND can be enumerated before MT5 has created and
                # populated its child controls, especially with two terminals.
                # Wait boundedly for the same exact supported controls/label.
                controls_deadline = time.monotonic() + 1
                while True:
                    label = self.u.GetDlgItem(dialog, 10405)
                    combo = self.u.GetDlgItem(dialog, 10139)
                    text = c.create_unicode_buffer(64)
                    if (label and self.class_name(label) == "Static" and combo and
                            self.class_name(combo) == "ComboBox"):
                        self.u.GetWindowTextW(label, text, len(text))
                        if text.value.replace("&", "").strip() == "Server:":
                            break
                    if time.monotonic() >= controls_deadline:
                        raise InventoryError("UNSUPPORTED_TERMINAL_UI")
                    time.sleep(0.05)
                if self.process_identity(pid, executable) != identity:
                    raise InventoryError("TERMINAL_CHANGED")
                count = self.send(combo, 0x146)
                if not 0 <= count <= 1000:
                    raise InventoryError("SERVER_LIST_LIMIT")
                names = []
                read_deadline = time.monotonic() + 8
                for index in range(count):
                    if time.monotonic() > read_deadline:
                        raise InventoryError("UI_TIMEOUT")
                    length = self.send(combo, 0x149, index)
                    if not 0 < length <= 128:
                        raise InventoryError("SERVER_NAME_INVALID")
                    # Keep the buffer alive until SendMessageTimeoutW completes.
                    buffer = c.create_unicode_buffer(length + 1)
                    if self.send(combo, 0x148, index, c.cast(buffer, c.c_void_p).value) != length:
                        raise InventoryError("SERVER_LIST_CHANGED")
                    names.append(buffer.value)
                if self.send(combo, 0x146) != count or self.process_identity(pid, executable) != identity:
                    raise InventoryError("TERMINAL_CHANGED")
                return sorted(set(names))
            finally:
                if dialog and self.u.IsWindow(dialog):
                    self.u.PostMessageW(dialog, 0x111, 2, 0)  # IDCANCEL; never IDOK.
                    # Two terminals can be processing their catalogs at once.
                    # Keep the slot locked until cancellation completes; a slow
                    # close must not be mistaken for a still-open login dialog.
                    deadline = time.monotonic() + 3
                    while self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog):
                        raise InventoryError("UI_CLEANUP_FAILED")


def collect_inventory(executable, data_path, inspect_ui=False):
    validate_data_path(executable, data_path)
    windows = WindowsTerminal()
    pid, identity = windows.find_process(executable)
    if pid is None:
        return {"status": "OFFLINE", "processId": None, "processIdentity": None, "catalogHash": None, "serverNames": [], "verificationMethod": "none", "errorCode": None}
    fingerprint = catalogue_fingerprint(data_path)
    names = windows.visible_servers(pid, executable, identity, managed_login=True) if inspect_ui else []
    if catalogue_fingerprint(data_path) != fingerprint or windows.process_identity(pid, executable) != identity:
        raise InventoryError("TERMINAL_CHANGED")
    return {"status": "STARTING", "processId": pid, "processIdentity": identity, "catalogHash": fingerprint, "serverNames": names, "verificationMethod": "login_dialog" if inspect_ui else "none", "errorCode": None}
