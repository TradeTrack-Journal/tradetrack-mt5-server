import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.collector.node_agent import AgentError
from scripts import watch_mt5_health


class WatchdogTests(unittest.TestCase):
    def test_diagnostic_inventory_failure_does_not_block_fenced_recovery(self):
        with TemporaryDirectory() as directory:
            log = Path(directory) / 'health.jsonl'
            config = {'slots': [{'id': 'one'}]}
            with patch('sys.argv', ['watch', '--config', 'config.json', '--log', str(log), '--once']), \
                    patch.object(watch_mt5_health, 'load_config', return_value=config), \
                    patch.object(watch_mt5_health, 'telemetry'), \
                    patch.object(watch_mt5_health, 'InventoryAgent') as agent, \
                    patch.object(watch_mt5_health, 'recover_quarantined', return_value='restarted') as recover:
                agent.return_value.remote_slots = {'one': {'quarantineIdentity': 'old-process'}}
                agent.return_value.client.call.side_effect = AgentError('API_RESPONSE_LIMIT')
                watch_mt5_health.main()
                recover.assert_called_once()
                results = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(results[0]['errorCode'], 'API_RESPONSE_LIMIT')
                self.assertEqual(results[1]['state'], 'restarted')
