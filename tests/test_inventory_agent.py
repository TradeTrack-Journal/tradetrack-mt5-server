import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.collector.node_agent import AgentError, InventoryAgent, NodeClient, NoRedirect, load_config
from app.collector.windows_inventory import InventoryError, catalogue_fingerprint, collect_inventory, validate_data_path


class InventoryTests(unittest.TestCase):
    def test_missing_catalogue_is_not_an_empty_inventory(self):
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaisesRegex(InventoryError, "CATALOGUE_UNAVAILABLE"):
                catalogue_fingerprint(path)

    def test_data_directory_must_match_installation(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            exe = root / "terminal64.exe"
            exe.touch()
            data = root / "data"
            data.mkdir()
            (data / "origin.txt").write_text(str(root / "other"), encoding="utf-16")
            with self.assertRaisesRegex(InventoryError, "DATA_PATH_MISMATCH"):
                validate_data_path(exe, data)
            (data / "origin.txt").write_text(str(root), encoding="utf-16")
            validate_data_path(exe, data)

    @patch("app.collector.windows_inventory.validate_data_path")
    @patch("app.collector.windows_inventory.WindowsTerminal")
    @patch("app.collector.windows_inventory.catalogue_fingerprint")
    def test_offline_never_reads_catalogue_or_opens_terminal(self, fingerprint, windows, _):
        windows.return_value.find_process.return_value = (None, None)
        result = collect_inventory("terminal64.exe", ".", inspect_ui=True)
        self.assertEqual(result["status"], "OFFLINE")
        self.assertEqual(result["serverNames"], [])
        fingerprint.assert_not_called()
        windows.return_value.visible_servers.assert_not_called()

    @patch("app.collector.windows_inventory.validate_data_path")
    @patch("app.collector.windows_inventory.WindowsTerminal")
    @patch("app.collector.windows_inventory.catalogue_fingerprint", return_value="a" * 64)
    def test_hash_alone_does_not_prove_presence(self, _, windows, __):
        windows.return_value.find_process.return_value = (12, "12:34")
        windows.return_value.process_identity.return_value = "12:34"
        result = collect_inventory("terminal64.exe", ".")
        self.assertEqual(result["verificationMethod"], "none")
        self.assertEqual(result["serverNames"], [])
        windows.return_value.visible_servers.assert_not_called()

    @patch("app.collector.windows_inventory.validate_data_path")
    @patch("app.collector.windows_inventory.WindowsTerminal")
    @patch("app.collector.windows_inventory.catalogue_fingerprint", side_effect=["a" * 64, "b" * 64])
    def test_changed_catalogue_discards_ui_evidence(self, _, windows, __):
        windows.return_value.find_process.return_value = (12, "12:34")
        windows.return_value.visible_servers.return_value = ["Example-Demo"]
        with self.assertRaisesRegex(InventoryError, "TERMINAL_CHANGED"):
            collect_inventory("terminal64.exe", ".", inspect_ui=True)


class AgentTests(unittest.TestCase):
    def test_transport_rejects_plaintext_and_credentials_in_url(self):
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com?token=a"):
            with self.assertRaises(AgentError):
                NodeClient(url, "node-1", "a" * 43)
        NodeClient("http://127.0.0.1:3315", "node-1", "a" * 43)

    def test_redirects_are_never_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    @patch("app.collector.node_agent.time.sleep")
    def test_retry_reuses_identical_body(self, sleep):
        client = NodeClient("https://api.example", "node-1", "a" * 43)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"accepted":true}'
        client.opener.open = MagicMock(side_effect=[OSError(), response])
        client.call("/report", {"sequence": 1})
        requests = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertEqual(len(requests), 2)
        sleep.assert_called_once()

    @patch("app.collector.node_agent.time.sleep")
    def test_auth_failure_stops_without_logging_response_body(self, sleep):
        client = NodeClient("https://api.example", "node-1", "a" * 43)
        client.opener.open = MagicMock(side_effect=HTTPError("https://api.example", 401, "sensitive", {}, None))
        with self.assertRaisesRegex(AgentError, "^API_HTTP_401$"):
            client.call("/config")
        sleep.assert_not_called()

    @patch("app.collector.node_agent.time.sleep")
    def test_server_error_retry_preserves_completion_identity_and_is_bounded(self, sleep):
        client = NodeClient("https://api.example", "node-1", "a" * 43)
        client.opener.open = MagicMock(side_effect=lambda *a, **k: (_ for _ in ()).throw(HTTPError("https://api.example", 500, "sensitive", {}, None)))
        with self.assertRaisesRegex(AgentError, '^API_HTTP_500$') as raised:
            client.call('/slots/slot-1/jobs/job-1/complete', {'leaseToken': 'same', 'fence': '2'})
        self.assertEqual(raised.exception.operation, 'complete')
        requests = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertEqual(len(requests), 3)
        self.assertEqual(len({request.data for request in requests}), 1)
        self.assertEqual(sleep.call_count, 2)

    def test_local_config_rejects_shared_terminal(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            slot = {"id": "slot-1", "executablePath": str(root / "terminal64.exe"), "dataPath": str(root)}
            config = {"apiBaseUrl": "https://api.example", "nodeId": "node-1", "slots": [slot, dict(slot, id="slot-2")]}
            file = root / "agent.json"
            file.write_text(json.dumps(config))
            with self.assertRaisesRegex(AgentError, "DUPLICATE_SLOT"):
                load_config(file)

    @patch("app.collector.node_agent.inventory_snapshot")
    def test_process_restart_establishes_new_generation(self, snapshot):
        slot = {"id": "slot-1", "executablePath": "terminal64.exe", "dataPath": "."}
        agent = InventoryAgent({"apiBaseUrl": "https://api.example", "nodeId": "node-1", "slots": [slot]}, "a" * 43)
        agent.remote_slots = {"slot-1": {"generation": None}}
        agent.client.call = MagicMock(return_value={"accepted": True})
        snapshot.side_effect = [dict(status="STARTING", processId=12, processIdentity=identity,
                                    catalogHash="a" * 64, serverNames=[], verificationMethod="none", errorCode=None)
                                for identity in ("12:34", "12:34", "12:99")]
        for _ in range(3):
            agent.report_slot(slot)
        calls = agent.client.call.call_args_list
        sessions = [call.args[1] for call in calls if call.args[0].endswith("/session")]
        reports = [call.args[1] for call in calls if call.args[0].endswith("/report")]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[1]["expectedGeneration"], sessions[0]["generation"])
        self.assertEqual([report["sequence"] for report in reports], [1, 2, 1])


if __name__ == "__main__":
    unittest.main()
