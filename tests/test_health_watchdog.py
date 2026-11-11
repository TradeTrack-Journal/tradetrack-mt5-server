import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.collector.node_agent import AgentError
from scripts import watch_mt5_health


class WatchdogTests(unittest.TestCase):
    def test_disk_full_does_not_prevent_quarantine_recovery(self):
        config = {'slots': [{'id': 'one'}], 'apiBaseUrl': 'https://example.invalid', 'nodeId': 'test'}
        with patch('sys.argv', ['watch', '--config', 'config.json', '--log', 'health.jsonl', '--once']), \
                patch.object(watch_mt5_health, 'load_config', return_value=config), \
                patch.object(watch_mt5_health, 'telemetry') as telemetry, \
                patch.object(watch_mt5_health, 'NodeClient'), \
                patch.object(watch_mt5_health, 'InventoryAgent') as agent, \
                patch.object(watch_mt5_health, 'UiRecoveryBudget') as budget, \
                patch.object(watch_mt5_health, 'recover_quarantined', return_value='restarted') as recover, \
                patch.object(Path, 'open', side_effect=OSError(28, 'No space left')):
            budget.return_value.observe.return_value = False
            agent.return_value.remote_slots = {'one': {'quarantineIdentity': 'old-process'}}
            agent.return_value.client.call.return_value = {'slots': []}
            watch_mt5_health.main()
            recover.assert_called_once()
            self.assertTrue(any(c.args[0].get('errorCode') == 'WORKER_LOG_WRITE_FAILED'
                                for c in telemetry.report.call_args_list))

    def test_worker_stdout_full_does_not_abort_collection_dispatch(self):
        from scripts import run_mt5_worker
        with patch.object(run_mt5_worker, '_log_directory', None), \
                patch.object(run_mt5_worker, 'telemetry') as telemetry, \
                patch('builtins.print', side_effect=OSError(28, 'No space left')):
            run_mt5_worker.emit({'slotId': 'demo-01', 'state': 'collected'})
            telemetry.report.assert_any_call({'errorCode': 'WORKER_LOG_WRITE_FAILED'})

    def test_drain_exits_continuous_watchdog_without_recovery_or_sleep(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / 'agent.json'
            (Path(directory) / 'worker.drain').touch()
            with patch('sys.argv', ['watch', '--config', str(config), '--log', str(Path(directory) / 'health.jsonl')]), \
                    patch.object(watch_mt5_health, 'load_config', return_value={'slots': [], 'apiBaseUrl':'https://example.invalid','nodeId':'test'}), \
                    patch.object(watch_mt5_health, 'telemetry') as telemetry, \
                    patch.object(watch_mt5_health, 'NodeClient'), \
                    patch.object(watch_mt5_health, 'InventoryAgent') as agent, \
                    patch.object(watch_mt5_health.time, 'sleep') as sleep:
                watch_mt5_health.main()
                agent.assert_not_called()
                sleep.assert_not_called()
                telemetry.flush.assert_called_once()

    def test_orphaned_python_process_prevents_duplicate_watchdog(self):
        with patch('sys.argv', ['watch', '--config', 'config.json']), \
                patch.object(watch_mt5_health, 'WindowsTerminal') as native, \
                patch.object(watch_mt5_health, 'main') as main:
            native.return_value.inventory_lock.return_value.__enter__.side_effect = RuntimeError('SLOT_BUSY')
            with self.assertRaisesRegex(RuntimeError, 'SLOT_BUSY'):
                watch_mt5_health.run_single_instance()
            main.assert_not_called()

    def test_diagnostic_inventory_failure_does_not_block_fenced_recovery(self):
        with TemporaryDirectory() as directory:
            log = Path(directory) / 'health.jsonl'
            config = {'slots': [{'id': 'one'}], 'apiBaseUrl':'https://example.invalid','nodeId':'test'}
            with patch('sys.argv', ['watch', '--config', 'config.json', '--log', str(log), '--once']), \
                    patch.object(watch_mt5_health, 'load_config', return_value=config), \
                    patch.object(watch_mt5_health, 'telemetry'), \
                    patch.object(watch_mt5_health, 'NodeClient'), \
                    patch.object(watch_mt5_health, 'InventoryAgent') as agent, \
                    patch.object(watch_mt5_health, 'recover_quarantined', return_value='restarted') as recover:
                agent.return_value.remote_slots = {'one': {'quarantineIdentity': 'old-process'}}
                agent.return_value.client.call.side_effect = AgentError('API_RESPONSE_LIMIT')
                watch_mt5_health.main()
                recover.assert_called_once()
                results = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(results[0]['errorCode'], 'API_RESPONSE_LIMIT')
                self.assertEqual(results[1]['state'], 'restarted')
