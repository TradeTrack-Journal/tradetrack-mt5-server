"""Bounded local demo probe; not the production session/authorization gate.

Run from repository root with python -m scripts.probe_mt5 --help.
Password is read without echo and passed to the child over stdin, never CLI/files.
Only aggregate diagnostics leave the child. A disposable terminal installation
is kept outside the repository with access restricted to the current Windows user.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def native_code(mt5):
    error = mt5.last_error()
    return error[0] if isinstance(error, tuple) and error and type(error[0]) is int else None


def running_terminal_matches(pid, path):
    """Check the explicitly selected live Windows process without launching it."""
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size, status = wintypes.DWORD(len(buffer)), wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value != 259:
            return False
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        return Path(buffer.value).resolve() == Path(path).resolve()
    finally:
        kernel.CloseHandle(handle)


def run_child(request):
    """Bounded child, secret over stdin, sanitized failures only."""
    payload = json.dumps(request)
    try:
        completed = subprocess.run([sys.executable, "-m", "scripts.probe_mt5", "--child"],
                                   input=payload, text=True, capture_output=True, timeout=75)
        if completed.returncode != 0:
            return {"ok": False, "phase": "child_exit"}
        result = json.loads(completed.stdout)
        return result if isinstance(result, dict) else {"ok": False, "phase": "child_protocol"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "child_timeout"}
    except (ValueError, OSError):
        return {"ok": False, "phase": "child_protocol"}
    finally:
        payload = None
        request.pop("password", None)


def audit_history_clock(mt5, request):
    """Compare bounded raw history ranges, without inferring a broker timezone.

    Repeat the original query after the wider query to distinguish a range issue
    from history warming. No result here may advance a production sync cursor.
    """
    from app.collector.contracts import HistoryWindow
    from app.collector.normalization import normalize_deals

    observed = datetime.now(timezone.utc).replace(microsecond=0)
    start = observed - timedelta(days=30)
    windows = (
        ("utc_end_before", HistoryWindow(start, observed)),
        ("extended_end", HistoryWindow(start, observed + timedelta(days=1))),
        ("utc_end_after", HistoryWindow(start, observed)),
    )
    samples = []
    for name, window in windows:
        raw = mt5.history_deals_get(*window.mt5_bounds())
        if raw is None:
            return {"ok": False, "error_code": "HISTORY_UNAVAILABLE", "native_code": native_code(mt5)}
        account = mt5.account_info()
        if account is None or account.login != request["login"] or account.server != request["server"] or account.trade_allowed:
            return {"ok": False, "error_code": "IDENTITY_DRIFT"}
        deals = normalize_deals(raw, window)
        trading = [d for d in deals if d.type in (0, 1)]
        closed = [d for d in trading if d.entry in (1, 2, 3)]
        samples.append({
            "query": name, "raw_count": len(raw), "normalized_count": len(deals),
            "requested_end_numeric": window.end_ms,
            "cash_operation_count": len(deals) - len(trading),
            "closing_deal_count": len(closed),
            "trading_profit": str(sum((Decimal(d.profit) for d in trading), Decimal(0))),
            "trading_costs": str(sum((Decimal(d.commission) + Decimal(d.swap) + Decimal(d.fee) for d in trading), Decimal(0))),
            "closing_deals": [dict(symbol=d.symbol, side=d.type, entry=d.entry,
                                   volume=d.volume, profit=d.profit,
                                   broker_reported_time=datetime.fromtimestamp(d.time_msc / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")) for d in closed],
        })
    return {"ok": True, "observed_at_utc": observed.isoformat(), "samples": samples,
            "note": "Raw time labels do not establish UTC instants or a historical broker offset."}


def child_probe():
    import MetaTrader5 as mt5
    from app.collector.contracts import DataError
    from app.collector.history import plan_recent_history
    from app.collector.normalization import normalize_deals, normalize_positions

    request = json.loads(sys.stdin.readline())
    password = request.pop("password")
    started = time.monotonic()
    result = {"ok": False, "phase": "initialize", "package_version": mt5.__version__}
    try:
        if "attach_pid" in request and not running_terminal_matches(request["attach_pid"], request["path"]):
            result["phase"] = "selected_terminal_not_running"
            return result
        # The native API has no strict attach-only switch. Check the selected PID
        # immediately before initialize; a native launch race still needs a
        # production supervisor. This probe never explicitly launches in attach mode.
        if not mt5.initialize(request["path"], login=request["login"], password=password,
                              server=request["server"], portable=request.get("portable", True), timeout=20000):
            result["native_code"] = native_code(mt5)
            return result
        result["phase"] = "login"
        if not mt5.login(request["login"], password=password, server=request["server"], timeout=20000):
            result["native_code"] = native_code(mt5)
            return result
        password = None
        result["phase"] = "identity"
        account, terminal = mt5.account_info(), mt5.terminal_info()
        expected_path = Path(request["path"]).parent.resolve()
        expected_data_path = Path(request.get("expected_data_path", expected_path)).resolve()
        identity_ok = account is not None and account.login == request["login"] and account.server == request["server"]
        path_ok = terminal is not None and Path(terminal.path).resolve() == expected_path and Path(terminal.data_path).resolve() == expected_data_path
        result.update(identity_matches=identity_ok, isolated_paths_match=path_ok)
        if not identity_ok or not path_ok:
            return result
        result.update(terminal_build=terminal.build, connected=terminal.connected,
                      account_trade_allowed=account.trade_allowed,
                      account_trade_expert=account.trade_expert,
                      python_trading_disabled=terminal.tradeapi_disabled,
                      margin_mode=account.margin_mode, currency=account.currency)
        if account.trade_allowed or not terminal.connected:
            result["phase"] = "read_guard_rejected"
            return result
        if request.get("history_audit"):
            result["history_clock_audit"] = audit_history_clock(mt5, request)
            result["ok"] = result["history_clock_audit"]["ok"]
            result["phase"] = "history_clock_audit"
            return result
        result["phase"] = "read"
        plan = plan_recent_history(datetime.now(timezone.utc))
        window = plan.native_window
        result.update(observed_at_utc=plan.observed_at_utc.isoformat(),
                      native_range_start_ms=window.start_ms, native_range_end_ms=window.end_ms,
                      timestamp_basis=plan.timestamp_basis,
                      utc_cursor_advance_allowed=plan.utc_cursor_advance_allowed)
        samples = []
        for _ in range(3):
            history = mt5.history_deals_get(*window.mt5_bounds())
            history_code = native_code(mt5) if history is None else None
            if history is None:
                result.update(error_code="HISTORY_UNAVAILABLE", native_code=history_code)
                return result
            positions = mt5.positions_get()
            positions_code = native_code(mt5) if positions is None else None
            if positions is None:
                result.update(error_code="POSITIONS_UNAVAILABLE", native_code=positions_code)
                return result
            current = mt5.account_info()
            if current is None or current.login != request["login"] or current.server != request["server"] or current.trade_allowed:
                result["phase"] = "identity_drift"
                return result
            deals = normalize_deals(history, window)
            positions = normalize_positions(positions)
            # Exercise the actual wire encoding without exporting account data.
            json.dumps([asdict(item) for item in deals], allow_nan=False)
            json.dumps([asdict(item) for item in positions], allow_nan=False)
            trading = [d for d in deals if d.type in (0, 1)]
            samples.append({"deals": len(deals), "positions": len(positions),
                            "closing_deals": sum(d.entry in (1, 2, 3) for d in trading),
                            "cash_operations": len(deals) - len(trading),
                            "trading_profit": str(sum((Decimal(d.profit) for d in trading), Decimal(0))),
                            "trading_costs": str(sum((Decimal(d.commission) + Decimal(d.swap) + Decimal(d.fee) for d in trading), Decimal(0))),
                            "history_error": history_code, "positions_error": positions_code})
            time.sleep(1)
        result.update(ok=True, phase="complete", samples=samples,
                      history_counts_changed_during_probe=len({s["deals"] for s in samples}) > 1,
                      history_completeness_verified=False,
                      entry_types=sorted({item.entry for item in deals}),
                      deal_types=sorted({item.type for item in deals}),
                      elapsed_seconds=round(time.monotonic() - started, 2))
        return result
    except DataError as error:
        result.update(error_code=error.code, field=error.field)
        return result
    except Exception:
        # Never print an exception/traceback containing request/native objects.
        result["error_code"] = "PROBE_FAILED"
        return result
    finally:
        password = None
        mt5.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--login", type=int)
    parser.add_argument("--server")
    parser.add_argument("--attach-pid", type=int, help="Use this already running terminal; do not create a copy or close it")
    parser.add_argument("--data-path", type=Path, help="Expected data directory of the already running terminal")
    parser.add_argument("--history-audit", action="store_true", help="Compare UTC-now vs padded native history bounds")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child_probe()))
        return
    if os.name != "nt" or not args.source or not args.source.is_file() or not args.login or not args.server:
        parser.error("Windows, --source terminal64.exe, --login and --server are required")
    if args.attach_pid is not None:
        if not args.data_path or not args.data_path.is_dir():
            parser.error("Attach mode requires the existing terminal's --data-path")
        if not running_terminal_matches(args.attach_pid, args.source):
            parser.error("The selected terminal process is not running at --source")
    # Fail instead of falling back to echoed stdin on a noninteractive terminal.
    if not sys.stdin.isatty():
        parser.error("Use an interactive terminal for hidden password input")
    password = getpass.getpass("Investor password (hidden): ")
    if not password:
        parser.error("Password is required")
    if args.attach_pid is not None:
        print("Using the selected running terminal; no clone or terminal restart.", flush=True)
        result = run_child(dict(path=str(args.source.resolve()), login=args.login, server=args.server,
                                password=password, attach_pid=args.attach_pid,
                                expected_data_path=str(args.data_path.resolve()),
                                portable=args.data_path.resolve() == args.source.parent.resolve(),
                                history_audit=args.history_audit))
        password = None
        result["selected_terminal_still_running"] = running_terminal_matches(args.attach_pid, args.source)
        result["mode"] = "attach"
        print(json.dumps(result, indent=2))
        return
    root = Path(os.environ["LOCALAPPDATA"]) / "TradeTrack" / "local-probes"
    root.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="mt5-", dir=root))
    # Apply ACL before copying or authorizing. No shared Users/Everyone access.
    identity = subprocess.check_output(["whoami"], text=True).strip()
    subprocess.run(["icacls", str(runtime), "/inheritance:r", "/grant:r",
                    f"{identity}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                   check=True, capture_output=True)
    exe = runtime / "terminal64.exe"
    shutil.copy2(args.source, exe)
    for name in ("MetaEditor64.exe", "metatester64.exe"):
        source = args.source.parent / name
        if source.is_file():
            shutil.copy2(source, runtime / name)
    (runtime / "Config").mkdir()
    for name in ("servers.dat", "terminal.lic"):
        source = args.source.parent / "Config" / name
        if source.is_file():
            shutil.copy2(source, runtime / "Config" / name)
    config = runtime / "probe.ini"
    config.write_text("[Common]\nKeepPrivate=0\nNewsEnable=0\n"
                      "[Experts]\nEnabled=0\nAllowLiveTrading=0\nAllowDllImport=0\n",
                      encoding="utf-16")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    terminal = subprocess.Popen([str(exe), "/portable", f"/config:{config}"],
                                startupinfo=startup, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    print("Isolated probe started; maximum child runtime 75 seconds.", flush=True)
    result = {}
    try:
        # A fresh installation expands bundled files before IPC becomes ready.
        # This is a bounded startup probe, not proof that broker history is ready.
        ready_until = time.monotonic() + 15
        while terminal.poll() is None and time.monotonic() < ready_until:
            logs = list((runtime / "Logs").glob("*.log"))
            if any("started for" in path.read_text(encoding="utf-16le", errors="replace") for path in logs):
                time.sleep(1)
                break
            time.sleep(0.25)
        if terminal.poll() is not None:
            result = {"ok": False, "phase": "terminal_start", "exit_code": terminal.returncode}
        else:
            result = run_child(dict(path=str(exe), login=args.login, server=args.server,
                                    password=password, history_audit=args.history_audit))
            password = None
    finally:
        # Own Popen handle only. Never kill all Python/MT5 processes by name.
        if terminal.poll() is None:
            terminal.terminate()
        terminal.wait(timeout=10)
        password = None
    # Fresh private directory provides a bounded research signal, not a general
    # production investor verifier. Only marker counts are exposed.
    journal = "\n".join(path.read_text(encoding="utf-16", errors="replace")
                         for path in (runtime / "Logs").glob("*.log"))
    result["journal_investor_mentions"] = journal.lower().count("investor")
    result["journal_readonly_mentions"] = journal.lower().count("read only")
    result["runtime_directory"] = str(runtime)
    result["terminal_stopped"] = terminal.poll() is not None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
