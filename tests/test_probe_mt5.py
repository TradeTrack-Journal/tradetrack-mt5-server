"""The demo probe must fail safely without exposing credentials or native errors."""

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.probe_mt5 import child_probe, run_child


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("fake-slot/terminal64.exe").resolve()
        self.request = dict(path=str(self.path), login=123, server="Test-Demo", password="synthetic-secret")
        self.account = SimpleNamespace(login=123, server="Test-Demo", trade_allowed=False,
                                       trade_expert=False, margin_mode=2, currency="USD")
        self.terminal = SimpleNamespace(path=str(self.path.parent), data_path=str(self.path.parent),
                                        build=1, connected=True, tradeapi_disabled=True)
        self.native = SimpleNamespace(
            __version__="test", initialize=Mock(return_value=True), login=Mock(return_value=True),
            account_info=Mock(return_value=self.account), terminal_info=Mock(return_value=self.terminal),
            history_deals_get=Mock(return_value=()), positions_get=Mock(return_value=()),
            shutdown=Mock(), last_error=Mock(return_value=(-6, "synthetic-secret native details")),
        )

    def run_probe(self):
        with patch.dict("sys.modules", {"MetaTrader5": self.native}), \
             patch("sys.stdin", io.StringIO(json.dumps(self.request))), \
             patch("scripts.probe_mt5.time.sleep"):
            result = child_probe()
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.native.shutdown.assert_called_once()
        return result

    def test_successful_empty_account_is_read_three_times_with_identity_guards(self):
        result = self.run_probe()
        self.assertTrue(result["ok"])
        self.assertEqual([s["deals"] for s in result["samples"]], [0, 0, 0])
        self.assertEqual(self.native.account_info.call_count, 4)
        self.assertTrue(self.native.initialize.call_args.kwargs["portable"])
        self.assertEqual(self.native.initialize.call_args.args, (str(self.path),))
        self.native.login.assert_called_once()

    def test_initialize_error_retains_only_code(self):
        self.native.initialize.return_value = False
        result = self.run_probe()
        self.assertEqual(result["native_code"], -6)
        self.native.login.assert_not_called()
        self.native.history_deals_get.assert_not_called()

    def test_failed_explicit_login_never_reads_cached_account(self):
        self.native.login.return_value = False
        result = self.run_probe()
        self.assertEqual(result["phase"], "login")
        self.native.account_info.assert_not_called()
        self.native.history_deals_get.assert_not_called()

    def test_same_login_wrong_server_rejects_read(self):
        self.account.server = "Other-Demo"
        result = self.run_probe()
        self.assertFalse(result["identity_matches"])
        self.native.history_deals_get.assert_not_called()

    def test_shared_data_directory_rejects_read(self):
        self.terminal.data_path = str(self.path.parent.parent)
        result = self.run_probe()
        self.assertFalse(result["isolated_paths_match"])
        self.native.history_deals_get.assert_not_called()

    def test_trading_permission_rejects_read(self):
        self.account.trade_allowed = True
        result = self.run_probe()
        self.assertEqual(result["phase"], "read_guard_rejected")
        self.native.history_deals_get.assert_not_called()

    def test_context_change_after_read_discards_result(self):
        wrong = SimpleNamespace(login=456, server="Test-Demo", trade_allowed=False)
        self.native.account_info.side_effect = [self.account, wrong]
        result = self.run_probe()
        self.assertEqual(result["phase"], "identity_drift")
        self.assertNotIn("samples", result)

    def test_history_error_is_captured_before_another_native_call(self):
        self.native.history_deals_get.return_value = None
        result = self.run_probe()
        self.assertEqual(result["error_code"], "HISTORY_UNAVAILABLE")
        self.assertEqual(result["native_code"], -6)
        self.native.positions_get.assert_not_called()

    def test_positions_error_is_not_a_flat_snapshot(self):
        self.native.positions_get.return_value = None
        result = self.run_probe()
        self.assertEqual(result["error_code"], "POSITIONS_UNAVAILABLE")
        self.assertFalse(result["ok"])

    def test_native_exception_is_not_exposed(self):
        self.native.initialize.side_effect = RuntimeError("synthetic-secret")
        result = self.run_probe()
        self.assertEqual(result["error_code"], "PROBE_FAILED")

    def test_missing_selected_process_never_calls_initialize(self):
        self.request["attach_pid"] = 321
        with patch("scripts.probe_mt5.running_terminal_matches", return_value=False):
            result = self.run_probe()
        self.assertEqual(result["phase"], "selected_terminal_not_running")
        self.native.initialize.assert_not_called()

    def test_attach_accepts_explicit_normal_mode_data_directory(self):
        self.request.update(attach_pid=321, portable=False,
                            expected_data_path=str(self.path.parent / "appdata"))
        self.terminal.data_path = self.request["expected_data_path"]
        with patch("scripts.probe_mt5.running_terminal_matches", return_value=True) as check:
            result = self.run_probe()
        self.assertTrue(result["ok"])
        self.assertFalse(self.native.initialize.call_args.kwargs["portable"])
        check.assert_called_once_with(321, str(self.path))

    def test_empty_success_does_not_claim_proven_history_completeness(self):
        result = self.run_probe()
        self.assertFalse(result["history_completeness_verified"])
        self.assertFalse(result["utc_cursor_advance_allowed"])


class ChildTransportTests(unittest.TestCase):
    def test_secret_is_only_in_stdin_and_removed_from_request(self):
        request = {"password": "synthetic-secret", "login": 123}
        response = SimpleNamespace(returncode=0, stdout='{"ok":true}')
        with patch("scripts.probe_mt5.subprocess.run", return_value=response) as run:
            self.assertTrue(run_child(request)["ok"])
        self.assertNotIn("synthetic-secret", repr(run.call_args.args))
        self.assertIn("synthetic-secret", run.call_args.kwargs["input"])
        self.assertNotIn("password", request)

    def test_timeout_does_not_expose_process_payload(self):
        request = {"password": "synthetic-secret"}
        error = subprocess.TimeoutExpired("child", 75, output="synthetic-secret")
        with patch("scripts.probe_mt5.subprocess.run", side_effect=error):
            result = run_child(request)
        self.assertEqual(result["phase"], "child_timeout")
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertNotIn("password", request)


if __name__ == "__main__":
    unittest.main()
