"""Regression coverage for allocation-visible subsets and mixed GPU occupancy."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'lib'
SCRIPT = (ROOT / 'probe_via_job.sh').read_text()
MACHINE = SCRIPT.split("python3 - <<'PY'\n", 1)[1].split('\nPY\nsleep', 1)[0]


def machine(gpus, processes):
    def query(cmd, **kwargs):
        if '--query-compute-apps=gpu_uuid,pid,process_name,used_memory' in cmd:
            if processes is None:
                raise subprocess.CalledProcessError(1, cmd)
            return processes
        return gpus
    out = io.StringIO()
    with patch('subprocess.check_output', side_effect=query), contextlib.redirect_stdout(out):
        exec(compile(MACHINE, 'probe_machine', 'exec'), {})
    return dict(line.split('|', 1) for line in out.getvalue().splitlines() if not line.startswith(('MACHINE_GPU|', 'MACHINE_PROC|')))


class ProbeCoverage(unittest.TestCase):
    def test_busy_and_clean_cards_are_counted_separately(self):
        result = machine('0, H20, 92181, 97871, 0, GPU-busy\n1, H20, 0, 97871, 0, GPU-clean\n',
                         'GPU-busy, 123, vllm, 92170\n')
        self.assertEqual(result['MACHINE_READY_COUNT'], '1')
        self.assertEqual(result['MACHINE_PROBED_COUNT'], '2')
        self.assertEqual(result['MACHINE_READY_UUIDS'], 'GPU-clean')
        self.assertEqual(result['MACHINE_OCCUPIED'], 'yes')

    def test_spare_memory_with_live_pid_is_not_exclusive(self):
        result = machine('0, H20, 17000, 97871, 0, GPU-shared\n', 'GPU-shared, 123, python, 17000\n')
        self.assertEqual(result['MACHINE_READY_COUNT'], '0')

    def test_missing_process_visibility_fails_closed(self):
        result = machine('0, H20, 0, 97871, 0, GPU-clean\n', None)
        self.assertEqual(result['MACHINE_READY_COUNT'], '0')
        self.assertEqual(result['MACHINE_OCCUPIED'], 'unknown')

    def test_malformed_memory_fails_closed(self):
        result = machine('0, H20, N/A, 97871, 0, GPU-unknown\n', '')
        self.assertEqual(result['MACHINE_PROBED_COUNT'], '0')
        self.assertEqual(result['MACHINE_READY_COUNT'], '0')

    def classify(self, probed, ready=0, age=0, occupied='yes'):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'gpu.env'
            state.write_text(f'PROBED_AT={int(time.time()) - age}\nFINAL_STATE=COMPLETED\nOCCUPIED={occupied}\nSCHED_STATE=IDLE\nSCHED_TOTAL_GPUS=8\nSCHED_ALLOC_GPUS=0\nGPU_MIN_FREE_MIB=5000\nGPU_READY_COUNT={ready}\nGPU_PROBED_COUNT={probed}\n')
            return subprocess.check_output(['bash', '-c', 'source "$1"; classify_node_readiness "$2" 600', '_', str(ROOT / 'node_state_lib.sh'), str(state)], text=True).strip()

    def test_two_busy_cards_do_not_blacklist_eight_card_node(self):
        self.assertEqual(self.classify(2), 'unverified')

    def test_complete_busy_coverage(self):
        self.assertEqual(self.classify(8), 'busy')

    def test_clean_subset_is_visible_without_passing_whole_node_gate(self):
        self.assertEqual(self.classify(8, ready=6), 'partial_ready')

    def test_old_busy_evidence_is_rechecked(self):
        self.assertEqual(self.classify(8, age=1000), 'stale')


if __name__ == '__main__':
    unittest.main()
