"""Run low-priority, lossless housekeeping of deferred MT5 vendor packages."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from app.collector.archive_storage import deduplicate
from app.collector.windows_inventory import WindowsTerminal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('--max-seconds', type=int, default=240)
    args = parser.parse_args()
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
    root = Path(os.environ['APPDATA']) / 'MetaQuotes' / 'Terminal'
    store = Path(args.store).absolute()
    # A mutex prevents overlapping manual/scheduled passes; no stale lock files.
    with WindowsTerminal().inventory_lock(str(store) + '-maintenance'):
        before = shutil.disk_usage(root).free
        result = deduplicate(root, store, max_seconds=args.max_seconds,
                             progress=lambda row: print(json.dumps(row), flush=True))
        result.update(at=datetime.now(timezone.utc).isoformat(), freeBefore=before,
                      freeAfter=shutil.disk_usage(root).free)
        result['diskNeedsAttention'] = result['freeAfter'] < 5 * 1024**3
        temporary = store / 'status.pending.json'
        temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
        temporary.replace(store / 'status.json')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
