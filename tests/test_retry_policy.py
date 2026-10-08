import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from retry_policy import retry_policy, launch_candidates


def node(name):
    return {'node': name, 'partition': 'gpu', 'scheduler_free': 1, 'state': 'IDLE'}


class RetryTests(unittest.TestCase):
    def test_policy_ignores_error_text(self):
        for reason in ('priority', 'network error', 'unrecognized driver failure'):
            self.assertEqual(retry_policy({'phase': 'WAITING_ALLOCATION', 'reason': reason}, 10)['delay_seconds'], 0)
            self.assertEqual(retry_policy({'phase': 'CHECKING_ALLOCATION', 'reason': reason}, 10)['delay_seconds'], 10)

    def test_handoff_and_unknown_phase_do_not_restart(self):
        for phase in ('HANDED_OFF', 'unknown', None):
            self.assertFalse(retry_policy({'phase': phase}, 10)['retry'])

    def test_pending_first_node_immediately_tries_second(self):
        calls = []
        def launch(n):
            calls.append(n['node'])
            return {'phase': 'WAITING_ALLOCATION' if n['node'] == 'a' else 'HANDED_OFF',
                    'state': 'PENDING_TIMEOUT' if n['node'] == 'a' else 'ADMITTED_NOT_GPU_VERIFIED'}
        waits = []
        result = launch_candidates(lambda: [node('a'), node('b')], launch, 3, 10, waits.append)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(waits, [])
        self.assertEqual(result['state'], 'ADMITTED_NOT_GPU_VERIFIED')

    def test_admission_failure_does_not_cool_other_candidates(self):
        calls = []
        def launch(n):
            calls.append(n['node'])
            return {'phase': 'CHECKING_ALLOCATION' if n['node'] == 'a' else 'HANDED_OFF',
                    'state': 'REJECTED' if n['node'] == 'a' else 'ADMITTED_NOT_GPU_VERIFIED'}
        result = launch_candidates(lambda: [node('a'), node('b')], launch)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(result['attempts'][0]['retry_policy']['action'], 'BACKOFF_CANDIDATE')

    def test_refreshes_scheduler_and_bounded_rounds(self):
        snapshots = iter([[], [node('a')], [node('b')]])
        calls = [];waits = []
        def launch(n):
            calls.append(n['node'])
            return {'phase': 'WAITING_ALLOCATION', 'state': 'PENDING_TIMEOUT'}
        result = launch_candidates(lambda: next(snapshots), launch, 3, 7, waits.append)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(waits, [7, 7])
        self.assertEqual(result['state'], 'NO_ADMITTED_GPU')

    def test_unknown_phase_stops_instead_of_launching_again(self):
        calls = []
        def launch(n):
            calls.append(n['node']);return {'state': 'UNKNOWN'}
        result = launch_candidates(lambda: [node('a'), node('b')], launch, 3)
        self.assertEqual(calls, ['a'])
        self.assertEqual(result['state'], 'STOPPED_REQUIRES_DIAGNOSTIC')
