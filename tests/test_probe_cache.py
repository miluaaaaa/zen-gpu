import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from probe_cache import context_key, probe_lock, reusable_busy


class ProbeCacheTests(unittest.TestCase):
    def setUp(self):
        self.state = {'PROBED_AT': '100', 'JOB_ID': '12', 'FINAL_STATE': 'COMPLETED',
                      'OCCUPIED': 'yes', 'GPU_READY_COUNT': '0', 'GPU_PROBED_COUNT': '8'}
        self.metadata = {'context': 'same', 'job_id': '12'}

    def reuse(self, **kwargs):
        return reusable_busy(self.state, self.metadata, kwargs.get('context', 'same'),
                             8, kwargs.get('cooldown', 30), 120, now=kwargs.get('now', 110))

    def test_busy_reuse_is_bounded_and_can_be_disabled(self):
        self.assertTrue(self.reuse())
        self.assertFalse(self.reuse(now=131))
        self.assertFalse(self.reuse(now=99))
        self.assertFalse(self.reuse(cooldown=0))

    def test_partial_coverage_or_ready_cards_are_never_reused(self):
        self.state['GPU_PROBED_COUNT'] = '2'
        self.assertFalse(self.reuse())
        self.state['GPU_PROBED_COUNT'] = '8'
        self.state['GPU_READY_COUNT'] = '1'
        self.assertFalse(self.reuse())

    def test_context_and_job_must_match(self):
        self.assertFalse(self.reuse(context='other-host'))
        self.metadata['job_id'] = 'old'
        self.assertFalse(self.reuse())

    def test_scheduler_and_memory_changes_invalidate_context(self):
        key = context_key('h', False, '/shared', None, {'allocated': 0}, 30000)
        self.assertNotEqual(key, context_key('h', False, '/shared', None, {'allocated': 1}, 30000))
        self.assertNotEqual(key, context_key('h', False, '/shared', None, {'allocated': 0}, 10000))

    def test_competing_clients_cannot_acquire_probe_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'probe.lock'
            with probe_lock(path) as first:
                self.assertTrue(first)
                with probe_lock(path) as second:
                    self.assertFalse(second)
            with probe_lock(path) as later:
                self.assertTrue(later)
