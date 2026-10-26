"""Exercise the offline demonstration without touching live data or transports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qq_character import cli


class DemoCliTests(unittest.TestCase):
    def test_live_sized_turn_budget_does_not_exhaust_offline_demo(self):
        config = json.loads((cli.ROOT / 'config/example.json').read_text(encoding='utf-8'))
        config['budgets'].update(turn_tokens=24000, daily_tokens=5280000,
                                 daily_model_calls=220, daily_replies=100)
        self.run_isolated_demo(config)

    def test_restrictive_live_budgets_do_not_disable_independent_fixture(self):
        config = json.loads((cli.ROOT / 'config/example.json').read_text(encoding='utf-8'))
        config['budgets'].update(turn_tokens=500, daily_tokens=500, daily_model_calls=1,
                                 daily_replies=1, tools_per_turn=0, input_chars=4000)
        self.run_isolated_demo(config)

    def run_isolated_demo(self, config):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / 'live.db'
            database.write_bytes(b'LIVE_DATABASE_SENTINEL_DO_NOT_OPEN')
            lab = root / 'live-skills'
            lab.mkdir()
            sentinel = lab / 'owner-data.txt'
            sentinel.write_bytes(b'OWNER_SKILL_DATA_DO_NOT_CHANGE')
            config.update(database=str(database), skill_lab=str(lab))
            original = deepcopy(config)
            with patch.object(cli, 'emit') as emit, patch.object(cli, 'Runtime') as runtime, \
                    patch.object(cli, 'read_source') as source, patch.object(cli, 'serve') as serve:
                self.assertEqual(cli.demo(config), 0)
                result = emit.call_args.args[0]
                self.assertEqual(result['verification'], 'MOCK_ONLY')
                self.assertTrue(result['passed'])
                self.assertTrue(all(result['checks'].values()))
                self.assertEqual(result['network_reads'], 0)
                self.assertEqual(result['qq_messages_sent'], 0)
                runtime.assert_not_called()
                source.assert_not_called()
                serve.assert_not_called()
            self.assertEqual(config, original)
            self.assertEqual(database.read_bytes(), b'LIVE_DATABASE_SENTINEL_DO_NOT_OPEN')
            self.assertEqual(sentinel.read_bytes(), b'OWNER_SKILL_DATA_DO_NOT_CHANGE')
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['live-skills', 'live.db'])


if __name__ == '__main__':
    unittest.main()
