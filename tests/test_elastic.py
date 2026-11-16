import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from elastic import active_job_ids, parse_job_states, target_from_arg, terminal_job_ids


class ElasticTests(unittest.TestCase):
    def test_parse_and_classify_scheduler_states(self):
        states = parse_job_states('101|RUNNING\n102|PENDING\n103|COMPLETED\n104|FAILED\n')
        self.assertEqual(active_job_ids(states), ['101', '102'])
        self.assertEqual(terminal_job_ids(states), ['103', '104'])

    def test_auto_target_tracks_current_scheduler_capacity(self):
        self.assertEqual(target_from_arg('auto', [{'scheduler_free': 2}, {'scheduler_free': 3}]), 5)

    def test_explicit_target_is_stable(self):
        self.assertEqual(target_from_arg('6', [{'scheduler_free': 1}]), 6)
        with self.assertRaises(ValueError):
            target_from_arg('0', [])


if __name__ == '__main__':
    unittest.main()
