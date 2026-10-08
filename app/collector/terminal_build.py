"""Read local executable version metadata before assigning an account to MT5."""
import ctypes as c
from ctypes import wintypes as w

VERIFIED_BUILDS = (6182, 6190, 6193, 6204, 6230, 6231, 6246)


def file_build(executable):
    version = c.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
    version.GetFileVersionInfoSizeW.restype = w.DWORD
    version.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p]
    version.GetFileVersionInfoW.restype = w.BOOL
    version.VerQueryValueW.argtypes = [c.c_void_p, w.LPCWSTR, c.POINTER(c.c_void_p), c.POINTER(w.UINT)]
    version.VerQueryValueW.restype = w.BOOL
    ignored = w.DWORD()
    size = version.GetFileVersionInfoSizeW(str(executable), c.byref(ignored))
    if not 0 < size <= 1024 * 1024:
        return None
    buffer = c.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(executable), 0, size, buffer):
        return None
    pointer, length = c.c_void_p(), w.UINT()
    if not version.VerQueryValueW(buffer, '\\', c.byref(pointer), c.byref(length)):
        return None
    if not pointer.value or length.value < 13 * c.sizeof(w.DWORD):
        return None
    fixed = c.cast(pointer, c.POINTER(w.DWORD))
    if fixed[0] != 0xFEEF04BD or fixed[2] != 5 << 16 or fixed[3] >> 16 != 0:
        return None
    return fixed[3] & 0xffff
