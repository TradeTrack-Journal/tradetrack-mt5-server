"""Outbound-only inventory reporter. No database connection or account passwords."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib import error, parse, request
from uuid import uuid4

from app.collector.windows_inventory import InventoryError, collect_inventory


class AgentError(Exception):
    def __init__(self, code, operation=None):
        super().__init__(code)
        self.operation = operation


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NodeClient:
    def __init__(self, base_url, node_id, token):
        url = parse.urlsplit(base_url)
        if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "::1"))) or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise AgentError("INVALID_API_URL")
        if not url.hostname or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node_id):
            raise AgentError("INVALID_NODE_CONFIG")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token or ""):
            raise AgentError("INVALID_AGENT_TOKEN")
        self.base = base_url.rstrip("/") + "/mt5/nodes/" + node_id
        self.token = token
        self.opener = request.build_opener(NoRedirect())

    def call(self, suffix, payload=None):
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        operation = suffix.rsplit('/', 1)[-1]
        if operation not in {'config', 'inventory', 'session', 'report', 'claim', 'credentials', 'heartbeat', 'complete', 'fail', 'prepare', 'preparation-missing'}:
            operation = 'request'
        for attempt in range(3):
            req = request.Request(self.base + suffix, data=data, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            retry_delay = min(2 ** attempt, 4)
            try:
                with self.opener.open(req, timeout=10) as response:
                    raw = response.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise AgentError("API_RESPONSE_LIMIT")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise AgentError("API_RESPONSE_INVALID")
                return result
            except error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "")
                exc.close()
                if status not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise AgentError(f"API_HTTP_{status}", operation) from None
                if retry_after.isdigit():
                    if int(retry_after) > 10:
                        raise AgentError("API_RETRY_LATER") from None
                    retry_delay = max(retry_delay, int(retry_after))
            except (OSError, error.URLError):
                if attempt == 2:
                    raise AgentError("API_UNAVAILABLE") from None
            except (ValueError, UnicodeError):
                raise AgentError("API_RESPONSE_INVALID") from None
            time.sleep(retry_delay)
        raise AgentError("API_UNAVAILABLE")


def load_config(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise AgentError("CONFIG_LIMIT")
        config = json.loads(raw)
        if not isinstance(config, dict) or set(config) != {"apiBaseUrl", "nodeId", "slots"}:
            raise AgentError("INVALID_NODE_CONFIG")
        if not isinstance(config["apiBaseUrl"], str) or not isinstance(config["nodeId"], str):
            raise AgentError("INVALID_NODE_CONFIG")
        slots = config["slots"]
        if not isinstance(slots, list) or not 1 <= len(slots) <= 16:
            raise AgentError("INVALID_NODE_CONFIG")
        ids, executables, data_paths = set(), set(), set()
        for slot in slots:
            if not isinstance(slot, dict) or set(slot) != {"id", "executablePath", "dataPath"}:
                raise AgentError("INVALID_SLOT_CONFIG")
            if not all(isinstance(value, str) for value in slot.values()) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slot["id"]):
                raise AgentError("INVALID_SLOT_CONFIG")
            if not Path(slot["executablePath"]).is_absolute() or not Path(slot["dataPath"]).is_absolute():
                raise AgentError("INVALID_SLOT_PATH")
            executable = str(Path(slot["executablePath"]).resolve()).casefold()
            data_path = str(Path(slot["dataPath"]).resolve()).casefold()
            if slot["id"] in ids or executable in executables or data_path in data_paths:
                raise AgentError("DUPLICATE_SLOT")
            ids.add(slot["id"])
            executables.add(executable)
            data_paths.add(data_path)
        return config
    except (OSError, ValueError, TypeError):
        raise AgentError("INVALID_NODE_CONFIG") from None


def inventory_snapshot(slot, inspect_ui):
    try:
        return collect_inventory(slot["executablePath"], slot["dataPath"], inspect_ui)
    except InventoryError as exc:
        return {"status": "ERROR", "processId": None, "processIdentity": None, "catalogHash": None,
                "serverNames": [], "verificationMethod": "none", "errorCode": str(exc)}


class InventoryAgent:
    def __init__(self, config, token):
        self.config = config
        self.client = NodeClient(config["apiBaseUrl"], config["nodeId"], token)
        self.sessions = {}
        self.remote_slots = {}
        self.last_snapshots = {}

    def connect(self):
        remote = self.client.call("/config")
        if remote.get("nodeId") != self.config["nodeId"] or not isinstance(remote.get("slots"), list):
            raise AgentError("API_CONFIG_INVALID")
        self.remote_slots = {slot["id"]: slot for slot in remote["slots"] if isinstance(slot, dict) and isinstance(slot.get("id"), str)}
        if set(self.remote_slots) != {slot["id"] for slot in self.config["slots"]}:
            raise AgentError("SLOT_CONFIG_MISMATCH")
        for slot in self.config["slots"]:
            other = self.remote_slots[slot["id"]]
            for field in ("executablePath", "dataPath"):
                if not isinstance(other.get(field), str) or Path(other[field]).resolve() != Path(slot[field]).resolve():
                    raise AgentError("SLOT_CONFIG_MISMATCH")

    def report_slot(self, slot, inspect_ui=False):
        snapshot = inventory_snapshot(slot, inspect_ui)
        current = self.sessions.get(slot["id"])
        if current is None or current["processIdentity"] != snapshot["processIdentity"]:
            generation = str(uuid4())
            expected = current["generation"] if current else self.remote_slots[slot["id"]].get("generation")
            self.client.call(f"/slots/{slot['id']}/session", {
                "generation": generation, "expectedGeneration": expected,
                "processId": snapshot["processId"], "processIdentity": snapshot["processIdentity"],
            })
            current = {"generation": generation, "sequence": 0, "processIdentity": snapshot["processIdentity"]}
            self.sessions[slot["id"]] = current
        current["sequence"] += 1
        payload = dict(snapshot, generation=current["generation"], sequence=current["sequence"],
                       observedAt=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        self.client.call(f"/slots/{slot['id']}/report", payload)
        self.last_snapshots[slot["id"]] = snapshot
        return {"slotId": slot["id"], "status": snapshot["status"], "visibleServerCount": len(snapshot["serverNames"]), "errorCode": snapshot["errorCode"]}
