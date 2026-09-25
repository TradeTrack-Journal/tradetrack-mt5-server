"""Run the shadow collector against configured, already running Windows terminals."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.collector import telemetry

_log_directory = None
_log_lock = Lock()


def emit(result):
    telemetry.report(result)
    line = json.dumps(dict(result, at=datetime.now(timezone.utc).isoformat()))
    if _log_directory is not None:
        try:
            with _log_lock:
                path = _log_directory / ('worker-' + datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.jsonl')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(line + '\n')
        except OSError:
            telemetry.report({'errorCode': 'WORKER_LOG_WRITE_FAILED'})
    print(line, flush=True)


def child():
    from app.collector.gateway import collect, CollectionError
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise CollectionError("INVALID_NATIVE_DATA")
        result = collect(json.loads(raw))
        output = json.dumps(result, allow_nan=False, separators=(",", ":"))
        if len(output.encode()) > 4 * 1024 * 1024:
            raise CollectionError("BATCH_TOO_LARGE")
        print(output)
    except CollectionError as exc:
        print(json.dumps({"errorCode": str(exc)}))
    except Exception:
        print(json.dumps({"errorCode": "CHILD_FAILED"}))


def main():
    global _log_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--builder", help="Dedicated credential-free server-builder terminal")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child()
        return
    telemetry.initialize()
    from app.collector.node_agent import load_config, AgentError
    from app.collector.worker import SlotWorker
    from app.collector.windows_inventory import InventoryError
    if not args.config:
        parser.error("--config is required")
    config = load_config(args.config)
    _log_directory = Path(args.config).resolve().parent
    token = os.environ.pop("MT5_AGENT_TOKEN", "")

    def consume(worker):
        slot = worker.slot
        try:
            emit(worker.once(allow_ui=False))
            return True
        except (AgentError, InventoryError) as exc:
            if str(exc) == 'TERMINAL_QUARANTINED':
                emit({"slotId": slot["id"], "state": "recovery_pending", "errorCode": str(exc)})
                return True
            if str(exc) in {'API_UNAVAILABLE', 'API_RETRY_LATER'}:
                emit({"slotId": slot["id"], "state": "retrying", "errorCode": str(exc)})
                return True
            if str(exc) == 'TERMINAL_CHANGED':
                worker.inspected_at = 0
                emit({"slotId": slot["id"], "state": "maintenance_required", "errorCode": str(exc)})
                return True
            emit({"slotId": slot["id"], "state": "stopped", "errorCode": str(exc), "operation": getattr(exc, 'operation', None)})
            return False
        except Exception:
            emit({"slotId": slot["id"], "state": "stopped", "errorCode": "WORKER_FAILED"})
            return False

    workers = [SlotWorker(config, token, slot) for slot in config['slots']]
    for worker in workers:
        worker.connect()
    failed = False
    from app.collector.server_preparation import ServerPreparation
    preparation = ServerPreparation(args.builder) if args.builder else None
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        while workers:
            inspected = [worker for worker in workers if worker.inspected_at]
            if preparation and inspected:
                try:
                    result = preparation.once(inspected)
                    if result:
                        emit(result)
                except (AgentError, InventoryError, OSError) as exc:
                    code = str(exc) if isinstance(exc, (AgentError, InventoryError)) else 'PREPARATION_IO_FAILED'
                    if not code or len(code) > 64 or not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in code):
                        code = 'PREPARATION_FAILED'
                    emit({'state': 'SERVER_PREPARATION_RETRY', 'errorCode': code})
            ready = []
            # Win32 dialog inspection is reliable on the main thread. Complete
            # this phase before starting native collection in parallel slots.
            for worker in workers:
                try:
                    failure = worker.prepare_inventory()
                    if failure:
                        emit(failure)
                        continue
                    ready.append(worker)
                except (AgentError, InventoryError) as exc:
                    emit({'slotId': worker.slot['id'], 'state': 'offline', 'errorCode': str(exc)})
            results = list(pool.map(consume, ready))
            stopped = [worker for worker, ok in zip(ready, results) if not ok]
            failed = failed or bool(stopped)
            workers = [worker for worker in workers if worker not in stopped]
            if args.once:
                failed = failed or len(ready) != len(workers)
                break
            if stopped:
                # Every native collection in this batch has finished. Let the
                # supervisor recreate the full pool instead of silently losing
                # one slot forever. Server-side leases/quarantine remain intact.
                break
            time.sleep(10)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception:
        emit({"errorCode": "WORKER_START_FAILED"})
        raise SystemExit(1)

    finally:
        telemetry.flush()
