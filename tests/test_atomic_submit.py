import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from allocation_gate import check
from atomic_submit import submit


class AdmissionTests(unittest.TestCase):
    def test_single_clean_card(self):
        calls = []
        def query(args):
            calls.append(args)
            return '' if '--query-compute-apps=gpu_uuid,pid' in args else 'GPU-a, 0, 80000, 0\n'
        uuid = check({'CUDA_VISIBLE_DEVICES': '0', 'SLURM_JOB_GPUS': '5'}, query, 30000)
        self.assertEqual(uuid, 'GPU-a')
        self.assertTrue(all('-i' not in args for args in calls))

    def test_unscoped_multi_card_visibility_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'one card'):
            check({'CUDA_VISIBLE_DEVICES': '0', 'SLURM_JOB_GPUS': '5'},
                  lambda args: 'GPU-a, 0, 80000, 0\nGPU-b, 0, 80000, 0\n', 0)

    def test_rejects_pid_even_with_spare_memory(self):
        def query(args):
            return 'GPU-a, 123\n' if '--query-compute-apps=gpu_uuid,pid' in args else 'GPU-a, 1, 80000, 0\n'
        with self.assertRaisesRegex(ValueError, 'occupied'):
            check({'CUDA_VISIBLE_DEVICES': '0', 'SLURM_JOB_GPUS': '0'}, query, 30000)

    def test_missing_or_multiple_allocations_fail_closed(self):
        for env in ({}, {'CUDA_VISIBLE_DEVICES': '0,1', 'SLURM_JOB_GPUS': '0'},
                    {'CUDA_VISIBLE_DEVICES': '0', 'SLURM_JOB_GPUS': '0,1'}):
            with self.assertRaises(ValueError):
                check(env, lambda args: '', 0)

    def test_missing_process_query_fails_closed(self):
        def query(args):
            if '--query-compute-apps=gpu_uuid,pid' in args:
                raise OSError('telemetry unavailable')
            return 'GPU-a, 0, 80000, 0\n'
        with self.assertRaises(OSError):
            check({'CUDA_VISIBLE_DEVICES': '0', 'SLURM_JOB_GPUS': '0'}, query, 0)


class SubmitTests(unittest.TestCase):
    def run_attempt(self, evidence, queue='RUNNING', timeout=15):
        calls = []
        def remote(command):
            calls.append(command)
            if command.startswith('cat -- '):
                return '#!/bin/bash\n#SBATCH --mem=48G\n#SBATCH --time=24:00:00\nexec python model.py\n'
            if command.startswith('sbatch '):
                return '321\n'
            if command.startswith('if test -f '):
                return json.dumps(evidence) if evidence else ''
            if command.startswith('squeue '):
                return queue
            return ''
        with patch('atomic_submit.time.sleep'), patch('atomic_submit.time.monotonic', side_effect=range(100)):
            result = submit(remote, {'node': 'node-a', 'partition': 'gpu'}, '/shared/lane.sbatch', '/shared/zen', 30000, timeout)
        return result, calls

    def test_admission_and_exec_share_one_submission_and_keep_allocation(self):
        result, calls = self.run_attempt({'state': 'ADMITTED_NOT_GPU_VERIFIED', 'gpu_uuid': 'GPU-a'})
        self.assertEqual(result['state'], 'ADMITTED_NOT_GPU_VERIFIED')
        self.assertEqual(sum(c.startswith('sbatch ') for c in calls), 1)
        wrapper = next(c for c in calls if 'set -euo pipefail' in c)
        self.assertLess(wrapper.index('gate-'), wrapper.index('exec bash'))
        self.assertIn('--mem=48G', wrapper)
        self.assertIn('--time=24:00:00', wrapper)
        self.assertFalse(any(c.startswith('scancel ') for c in calls))

    def test_rejection_cancels_only_own_job(self):
        result, calls = self.run_attempt({'state': 'REJECTED', 'reason': 'occupied'})
        self.assertEqual(result['state'], 'REJECTED')
        self.assertEqual([c for c in calls if c.startswith('scancel ')], ['scancel 321'])

    def test_pending_is_timeout_not_no_clean_card(self):
        result, calls = self.run_attempt(None, 'PENDING', 2)
        self.assertEqual(result['state'], 'PENDING_TIMEOUT')
        self.assertIn('scancel 321', calls)

    def test_finished_without_admission_is_failure(self):
        result, calls = self.run_attempt(None, '')
        self.assertEqual(result['state'], 'ADMISSION_FAILED')
        self.assertIn('scancel 321', calls)

    def test_array_directive_is_rejected_before_submission(self):
        calls = []
        def remote(command):
            calls.append(command)
            return '#!/bin/bash\n#SBATCH --array=0-7\n'
        with self.assertRaisesRegex(ValueError, 'multi-lane'):
            submit(remote, {'node': 'a', 'partition': 'gpu'}, '/lane', '/zen', 1, 1)
        self.assertFalse(any(c.startswith('sbatch ') for c in calls))

    def test_transport_error_cancels_own_attempt(self):
        calls = []
        def remote(command):
            calls.append(command)
            if command.startswith('cat -- '): return '#!/bin/bash\n'
            if command.startswith('sbatch '): return '456\n'
            if command.startswith('if test -f '): raise OSError('lost connection')
            return ''
        with self.assertRaises(OSError):
            submit(remote, {'node': 'a', 'partition': 'gpu'}, '/lane', '/zen', 1, 1)
        self.assertIn('scancel 456', calls)


if __name__ == '__main__':
    unittest.main()
