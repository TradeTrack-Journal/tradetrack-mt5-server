"""Run the shadow collector against configured, already running Windows terminals."""

import argparse
import json
import os
import sys
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
    parser.add_argument("--drain-file", help="Stop claiming and exit after in-flight jobs finish when this file exists")
    parser.add_argument("--builder", help="Dedicated credential-free server-builder terminal")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child()
        return
    telemetry.initialize(Path(args.config).resolve().parent / 'telemetry-state.sqlite3' if args.config else None)
    from app.collector.node_agent import load_config
    from app.collector.worker import SlotWorker
    if not args.config:
        parser.error("--config is required")
    config = load_config(args.config)
    _log_directory = Path(args.config).resolve().parent
    token = os.environ.pop("MT5_AGENT_TOKEN", "")

    from app.collector.scheduler import run_slots
    from app.collector.server_preparation import ServerPreparation
    workers = [SlotWorker(config, token, slot) for slot in config['slots']]
    preparation = ServerPreparation(args.builder) if args.builder else None
    drained = lambda: bool(args.drain_file and Path(args.drain_file).exists())
    ok = run_slots(workers, emit, preparation, once=args.once, stop=drained)
    if args.once and not ok:
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
