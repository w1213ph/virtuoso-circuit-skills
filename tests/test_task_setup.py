"""Task setup is offline, leaves evidence unfilled and protects existing work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/paper-to-virtuoso/scripts/new_task.py'

class TaskSetup(unittest.TestCase):
    def run_setup(self, mode, dest):
        return subprocess.run([sys.executable, str(SCRIPT), '--project', 'AFE', '--mode', mode,
                               '--out', str(dest)], capture_output=True, text=True)

    def test_new_design_and_paper_start_without_completed_results(self):
        with tempfile.TemporaryDirectory() as td:
            for mode in ['design', 'paper']:
                dest = Path(td) / mode
                result = self.run_setup(mode, dest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((dest / 'requirements.json').read_text())['mode'], mode)
                self.assertTrue((dest / 'DESIGN_PLAN.md').is_file())
                self.assertEqual(len((dest / 'optimization-candidates.csv').read_text().splitlines()), 1)
                acceptance = json.loads((dest / 'acceptance.json').read_text())
                for check in acceptance['gui_visual_review']['checks'].values():
                    self.assertEqual(check['status'], 'not_verified')
                    self.assertEqual(check['evidence'], [])
                self.assertEqual(acceptance['gui_visual_review']['reviewed_views'], [])
                self.assertEqual(acceptance['physical_implementation']['status'], 'not_verified')
                self.assertIsNone(acceptance['physical_implementation']['area_budget'])
                self.assertEqual(acceptance['physical_implementation']['large_device_exceptions'], [])
                for name in ['evidence', 'screenshots', 'netlists', 'simulation']:
                    self.assertEqual(list((dest / name).iterdir()), [])

    def test_existing_task_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'task'
            self.assertEqual(self.run_setup('design', dest).returncode, 0)
            note = dest / 'DESIGN_PLAN.md'
            note.write_bytes(b'User work must survive\n')
            before = {str(f.relative_to(dest)): f.read_bytes() for f in dest.rglob('*') if f.is_file()}
            self.assertNotEqual(self.run_setup('paper', dest).returncode, 0)
            after = {str(f.relative_to(dest)): f.read_bytes() for f in dest.rglob('*') if f.is_file()}
            self.assertEqual(before, after)

    def test_non_electrical_tasks_keep_their_scope(self):
        with tempfile.TemporaryDirectory() as td:
            for mode in ['redraw', 'environment', 'export']:
                dest = Path(td) / mode
                result = self.run_setup(mode, dest)
                self.assertEqual(result.returncode, 0, result.stderr)
                req = json.loads((dest / 'requirements.json').read_text())
                self.assertFalse(req['electrical_changes_authorized'])
                self.assertFalse(req['new_simulations_authorized'])
                self.assertFalse((dest / 'optimization-candidates.csv').exists())

if __name__ == '__main__':
    unittest.main()
