"""Проверки нового экспериментального запуска; не измерения модели."""
import sys
sys.dont_write_bytecode = True
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import run_offline as experiment

class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((ROOT / 'protocol_offline.json').read_text())
        cls.rows = experiment.planned_rows(cls.protocol)

    def test_source_matches(self):
        self.assertTrue(experiment.verify_source()['matched'])

    def test_count_and_versions(self):
        self.assertEqual(len(self.rows), 120)
        self.assertEqual(sum(r['target_version'] == 'before' for r in self.rows), 60)
        self.assertEqual(sum(r['target_version'] == 'after' for r in self.rows), 60)

    def test_boundary_not_replicated(self):
        rows = [r for r in self.rows if r['method'] == 'boundary_grid']
        self.assertEqual(len(rows), 30)
        self.assertEqual({r['seed'] for r in rows}, {0})

    def test_random_seeds_distinct(self):
        rows = [r for r in self.rows if r['method'] == 'hypothesis']
        self.assertEqual(len(rows), 90)
        self.assertEqual({r['seed'] for r in rows}, {0, 1, 2})

    def test_balanced_cells(self):
        cells = {(r['condition'], r['limit'], r['method'], r['seed'], r['target_version']) for r in self.rows}
        self.assertEqual(len(cells), len(self.rows))
        for r in self.rows:
            opposite = 'before' if r['target_version'] == 'after' else 'after'
            self.assertIn((r['condition'], r['limit'], r['method'], r['seed'], opposite), cells)

    def test_original_proposals_unchanged(self):
        sys.path.insert(0, str(ROOT / 'source' / 'app'))
        import property_preparation as prep
        proposals, provenance = experiment.load_proposals(self.protocol, prep)
        self.assertEqual(len(proposals), 5)
        self.assertIn('sha256', provenance['model_narrow'])
        self.assertEqual(proposals['model_narrow']['domain'], [[-10, 10], [-10, 10]])
        self.assertNotIn('record_path', provenance['manual_narrow'])

    def test_errors_not_successful_exports(self):
        row = {'condition': 'x', 'method': 'boundary_grid', 'limit': 16,
               'target_version': 'before', 'state': 'EXPORT_ERROR',
               'search': {'outcome': 'REGRESSION_CONFIRMED'},
               'export': {'confirmed': False, 'process_attempts': 2}}
        summary = experiment.summarize([row])[0]
        self.assertEqual(summary['before_found'], 1)
        self.assertEqual(summary['before_export_confirmed'], 0)
        self.assertEqual(summary['errors'], 1)

    def test_not_started_kept(self):
        summary = experiment.summarize(self.rows)
        self.assertEqual(sum(x['not_started'] for x in summary), 120)

    def test_network_guard(self):
        with self.assertRaises(PermissionError):
            experiment.block_network('socket.connect', ())
        experiment.block_network('open', ())

if __name__ == '__main__':
    unittest.main(verbosity=2)
