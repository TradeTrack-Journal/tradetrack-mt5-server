"""Durable lease consumer; each MT5 call runs in its own bounded subprocess."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from uuid import uuid4

from .node_agent import AgentError, InventoryAgent
from .windows_inventory import InventoryError, WindowsTerminal, catalogue_fingerprint


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_child(payload, heartbeat, timeout=125, heartbeat_interval=20):
    """Only child PID is terminated. stderr is never surfaced (native may echo secrets)."""
    process = subprocess.Popen([sys.executable, "-m", "scripts.run_mt5_worker", "--child"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = bytearray()
    oversized = False

    def read():
        nonlocal oversized
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            output.extend(chunk)
            if len(output) > 4 * 1024 * 1024:
                oversized = True
                process.kill()
                return

    pool = ThreadPoolExecutor(max_workers=2)
    reader = pool.submit(read)
    heartbeat_future = None
    try:
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()
        payload["credentials"].pop("investorPassword", None)
        deadline, ping_at = time.monotonic() + timeout, time.monotonic() + heartbeat_interval
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                return {"errorCode": "CHILD_TIMEOUT"}
            if heartbeat_future is not None and heartbeat_future.done():
                heartbeat_future.result()
                heartbeat_future = None
            if time.monotonic() >= ping_at and heartbeat_future is None:
                heartbeat_future = pool.submit(heartbeat)
                ping_at = time.monotonic() + heartbeat_interval
            time.sleep(0.1)
        reader.result(timeout=5)
        if oversized:
            return {"errorCode": "BATCH_TOO_LARGE"}
        if process.returncode:
            return {"errorCode": "CHILD_FAILED"}
        result = json.loads(output)
        return result if isinstance(result, dict) else {"errorCode": "CHILD_FAILED"}
    except (ValueError, OSError):
        return {"errorCode": "CHILD_FAILED"}
    finally:
        payload["credentials"].pop("investorPassword", None)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        # HTTP heartbeat latency must not delay the native-child watchdog.
        pool.shutdown(wait=False, cancel_futures=True)
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


class SlotWorker:
    def __init__(self, config, token, slot):
        self.inventory = InventoryAgent(config, token)
        self.client = self.inventory.client
        self.slot = slot
        self.inspected_at = 0
        self.catalog_hash = None
        self.windows = WindowsTerminal()
        self.restart_identity = None
        self.offline_restart_after = 0

    def connect(self):
        self.inventory.connect()

    def prepare_inventory(self):
        slot = self.slot
        if self.restart_identity:
            # Only set after the native child exited and /fail acknowledged its lease.
            # Recovery runs on the UI thread and never stops other slots.
            from .server_preparation import close_terminal, start_terminal
            with self.windows.inventory_lock(slot['executablePath']):
                pid, identity = self.windows.find_process(slot['executablePath'])
                if identity == self.restart_identity:
                    close_terminal(self.windows, slot['executablePath'])
                    pid = None
                if not pid:
                    start_terminal(slot['executablePath'])
                self.restart_identity = None
                self.inspected_at = 0
            return {'slotId': slot['id'], 'state': 'restarting', 'errorCode': None}
        # Maintenance between jobs only; no dialogs while a native child holds the slot.
        if time.monotonic() - self.inspected_at >= 90:
            report = self.inventory.report_slot(slot, inspect_ui=True)
            if report["status"] == "OFFLINE" and time.monotonic() >= self.offline_restart_after:
                # report_slot has fenced the absent process/session with the API.
                # Only launch a missing configured terminal, never replace a live process.
                from .server_preparation import start_terminal
                with self.windows.inventory_lock(slot['executablePath']):
                    if self.windows.find_process(slot['executablePath'])[0] is None:
                        self.offline_restart_after = time.monotonic() + 60
                        start_terminal(slot['executablePath'])
                self.inspected_at = 0
                return {'slotId': slot['id'], 'state': 'restarting', 'errorCode': None}
            if report["status"] != "STARTING":
                return {"slotId": slot["id"], "state": "offline", "errorCode": report["errorCode"]}
            self.catalog_hash = self.inventory.last_snapshots[slot["id"]]["catalogHash"]
            self.inspected_at = time.monotonic()
        return None

    def once(self, allow_ui=True):
        slot = self.slot
        if allow_ui:
            failure = self.prepare_inventory()
            if failure:
                return failure
        elif not self.inspected_at or time.monotonic() - self.inspected_at >= 90:
            return {"slotId": slot["id"], "state": "maintenance_required"}
        session = self.inventory.sessions[slot["id"]]
        base = f"/slots/{quote(slot['id'], safe='')}/jobs"
        with self.windows.inventory_lock(slot["executablePath"]):
            pid, identity = self.windows.find_process(slot["executablePath"])
            if not identity or identity != session["processIdentity"] or catalogue_fingerprint(slot["dataPath"]) != self.catalog_hash:
                self.inspected_at = 0
                raise AgentError("TERMINAL_CHANGED")
            response = self.client.call(base + "/claim", {"generation": session["generation"], "claimId": str(uuid4()), "requestedAt": utc_now()})
            job = response.get("job")
            if job is None:
                return {"slotId": slot["id"], "state": "idle"}
            if not isinstance(job, dict) or job.get("generation") != session["generation"]:
                raise AgentError("API_JOB_INVALID")
            lease = {key: job[key] for key in ("generation", "leaseToken", "fence")}
            path = base + "/" + quote(job["id"], safe="")
            credentials = self.client.call(path + "/credentials", lease)
            payload = {"slot": slot, "processId": pid, "processIdentity": identity, "credentials": credentials}
            remaining = (datetime.fromisoformat(job["leaseUntil"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
            if remaining < 40:
                credentials.pop("investorPassword", None)
                raise AgentError("LEASE_DEADLINE_TOO_CLOSE")
            result = run_child(payload, lambda: self.client.call(path + "/heartbeat", lease), timeout=min(125, remaining - 35))
            if "errorCode" in result:
                self.client.call(path + "/fail", dict(lease, errorCode=result["errorCode"]))
                if result["errorCode"] in ("CHILD_TIMEOUT", "CHILD_FAILED", "IDENTITY_DRIFT", "TERMINAL_CHANGED"):
                    self.restart_identity = identity
                    raise AgentError("TERMINAL_QUARANTINED")
                return {"slotId": slot["id"], "state": "failed", "errorCode": result["errorCode"]}
            if self.windows.process_identity(pid, slot["executablePath"]) != identity:
                self.client.call(path + "/fail", dict(lease, errorCode="TERMINAL_CHANGED"))
                self.restart_identity = identity
                raise AgentError("TERMINAL_QUARANTINED")
            self.client.call(path + "/complete", dict(result, **lease))
            return {"slotId": slot["id"], "state": "collected", "dealCount": len(result["deals"]), "positionCount": len(result["positions"])}
