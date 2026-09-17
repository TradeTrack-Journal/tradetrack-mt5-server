"""Fail-closed read adapter. Imported inside a disposable child, never the agent."""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import re
import time

from .contracts import DataError, EPOCH, HistoryWindow
from .history import plan_recent_history, plan_history_before
from .normalization import normalize_account, normalize_deals, normalize_positions
from .clock_observation import sample as clock_sample, record as record_clock
from .windows_inventory import InventoryError, WindowsTerminal, validate_data_path


class CollectionError(Exception):
    pass


def journal_baseline(data_path):
    paths = list((Path(data_path) / "logs").glob("*.log"))
    if len(paths) > 512:
        raise CollectionError("JOURNAL_UNAVAILABLE")
    return {p.name: (p.stat().st_ino, p.stat().st_size) for p in paths}


def investor_evidence(data_path, baseline, login):
    """Only fresh appended English journal messages; old/cached evidence is rejected."""
    for path in (Path(data_path) / "logs").glob("*.log"):
        stat = path.stat()
        inode, offset = baseline.get(path.name, (stat.st_ino, 0))
        if inode != stat.st_ino or stat.st_size < offset or stat.st_size - offset > 1024 * 1024:
            raise CollectionError("JOURNAL_UNAVAILABLE")
        if stat.st_size == offset:
            continue
        with path.open("rb") as stream:
            stream.seek(offset)
            fresh = stream.read(1024 * 1024 + 1)
        if len(fresh) > 1024 * 1024 or len(fresh) % 2:
            raise CollectionError("JOURNAL_UNAVAILABLE")
        lines = fresh.decode("utf-16-le").splitlines()
        expected = f"'{login}': trading has been disabled - investor mode"
        if any(line.split("\t")[-1].strip() == expected for line in lines):
            return True
    return False


def collect(request, native=None, windows=None):
    if native is None:
        import MetaTrader5 as native
    windows = windows or WindowsTerminal()
    slot, credentials = request["slot"], request["credentials"]
    executable, data_path = slot["executablePath"], slot["dataPath"]
    login, server = credentials["login"], credentials["serverName"]
    if not isinstance(login, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", login):
        raise CollectionError("INVALID_NATIVE_DATA")
    validate_data_path(executable, data_path)
    verified_build = None
    trading_mode = None
    consented = credentials.get("allowTradingPassword") is True

    def identity():
        nonlocal verified_build, trading_mode
        if windows.process_identity(request["processId"], executable) != request["processIdentity"]:
            raise CollectionError("TERMINAL_CHANGED")
        account, terminal = native.account_info(), native.terminal_info()
        if (account is None or terminal is None or account.login != int(login) or account.server != server or
                Path(terminal.path).resolve() != Path(executable).parent.resolve() or Path(terminal.data_path).resolve() != Path(data_path).resolve()):
            raise CollectionError("IDENTITY_DRIFT")
        if not terminal.connected:
            raise CollectionError("CONNECTION_FAILED")
        if account.trade_allowed and not consented:
            raise CollectionError("TRADING_ENABLED")
        if trading_mode is not None and trading_mode != bool(account.trade_allowed):
            raise CollectionError("IDENTITY_DRIFT")
        trading_mode = bool(account.trade_allowed)
        if not terminal.tradeapi_disabled:
            raise CollectionError("PYTHON_TRADING_ENABLED")
        if not trading_mode and not consented and not windows.read_only_session(request["processId"], executable, request["processIdentity"], login, server, terminal.build):
            raise CollectionError("INVESTOR_UNVERIFIED")
        if verified_build is not None and verified_build != terminal.build:
            raise CollectionError("IDENTITY_DRIFT")
        verified_build = terminal.build
        return account

    try:
        if windows.process_identity(request["processId"], executable) != request["processIdentity"]:
            raise CollectionError("TERMINAL_CHANGED")
        # Native API has no attach-only switch. Identity before/after fences its launch race.
        if not native.initialize(executable, login=int(login), password=credentials["investorPassword"], server=server,
                                 portable=Path(data_path).resolve() == Path(executable).parent.resolve(), timeout=20000):
            raise CollectionError("CONNECTION_FAILED")
        if not native.login(int(login), password=credentials["investorPassword"], server=server, timeout=20000):
            raise CollectionError("CONNECTION_FAILED")
        credentials.pop("investorPassword", None)
        # Native login can complete before the Windows caption is repainted.
        # Only the initial caption may settle; every retry rechecks account,
        # paths, process identity and both trading restrictions. Reads stay gated.
        caption_deadline = time.monotonic() + 2
        while True:
            try:
                identity()
                break
            except CollectionError as error:
                if str(error) != "INVESTOR_UNVERIFIED" or time.monotonic() >= caption_deadline:
                    raise
                time.sleep(0.05)
        plan = plan_recent_history(datetime.now(timezone.utc))
        before_ms = credentials.get("historyBeforeMs")
        window = plan_history_before(before_ms) if before_ms is not None else plan.native_window
        count_verified = callable(getattr(native, "history_deals_total", None))

        def count(bounds):
            value = native.history_deals_total(*bounds)
            if type(value) is not int or value < 0:
                raise CollectionError("HISTORY_UNAVAILABLE")
            return value

        if count_verified and before_ms is not None:
            # Shrink dense historical windows; retain the fixed upper cursor so nothing is skipped.
            for _ in range(32):
                if count(window.mt5_bounds()) <= 4000:
                    break
                if window.end_ms - window.start_ms <= 1000:
                    raise CollectionError("BATCH_TOO_LARGE")
                midpoint_ms = (window.start_ms + window.end_ms) // 2000 * 1000
                window = HistoryWindow(datetime.fromtimestamp(midpoint_ms / 1000, timezone.utc), window.end)
            else:
                raise CollectionError("BATCH_TOO_LARGE")
        # A warm-up read does not prove completeness; store only the final successful sample.
        first = native.history_deals_get(*window.mt5_bounds())
        if first is None:
            raise CollectionError("HISTORY_UNAVAILABLE")
        initial_deals = normalize_deals(first, window, max_records=5000)
        clock_symbols = {deal.symbol for deal in initial_deals if deal.symbol}
        clock_first = clock_sample(native, clock_symbols)
        identity()
        time.sleep(1)
        clock_second = clock_sample(native, clock_symbols)
        identity()
        record_clock(server, clock_first, clock_second)
        expected = count(window.mt5_bounds()) if count_verified else None
        final = native.history_deals_get(*window.mt5_bounds())
        deals = normalize_deals(final, window, max_records=5000)
        if count_verified and (len(final) != expected or count(window.mt5_bounds()) != expected):
            raise CollectionError("HISTORY_UNAVAILABLE")
        older_empty = False
        if count_verified:
            # Conservative inclusive native endpoint: a boundary deal can delay completion, never skip it.
            older_bounds = (EPOCH, window.start.replace(microsecond=0))
            older_empty = count(older_bounds) == 0 and count(older_bounds) == 0
        identity()
        positions = normalize_positions(native.positions_get(), max_records=5000)
        account_info = identity()
        account = normalize_account({"balance": account_info.balance, "equity": account_info.equity, "currency": account_info.currency})
        if len(deals) + len(positions) > 5000:
            raise CollectionError("BATCH_TOO_LARGE")
        return {"schemaVersion": 1, "login": login, "serverName": server,
                "observedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "timestampBasis": "broker_reported_unresolved", "historyCompletenessVerified": False,
                "investorVerified": not consented and not trading_mode, "pythonTradingDisabled": True,
                "investorVerificationMethod": "trading_password_consent" if consented else "terminal_title_read_only", "terminalBuild": verified_build,
                "account": asdict(account),
                "windowStartMs": window.start_ms, "windowEndMs": window.end_ms,
                **({"rangeCountVerified": True, "olderHistoryEmpty": older_empty} if count_verified else {}),
                **({"backfillBeforeMs": before_ms} if before_ms is not None else {}),
                "deals": [asdict(item) for item in deals], "positions": [asdict(item) for item in positions]}
    except DataError as exc:
        code = exc.code if exc.code in ("HISTORY_UNAVAILABLE", "POSITIONS_UNAVAILABLE", "BATCH_TOO_LARGE") else "INVALID_NATIVE_DATA"
        raise CollectionError(code) from None
    except InventoryError:
        raise CollectionError("TERMINAL_CHANGED") from None
    except (OSError, UnicodeError):
        raise CollectionError("JOURNAL_UNAVAILABLE") from None
    finally:
        credentials.pop("investorPassword", None)
        native.shutdown()  # Disconnect IPC only; never terminate a user's terminal.
