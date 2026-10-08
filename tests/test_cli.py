import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import time
import unittest
from unittest.mock import patch

CLI = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/zen'))
SNAPSHOT = 'NodeName=accelerator-a State=IDLE Partitions=gpu CfgTRES=cpu=96,gres/gpu=8 AllocTRES=cpu=0\n'


class CliTests(unittest.TestCase):
    def test_scheduler_gpu_counts_not_cpu_counts(self):
        nodes = CLI['parse_nodes'](SNAPSHOT + 'NodeName=cpu-a State=IDLE CfgTRES=cpu=128 AllocTRES=cpu=0\n')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['scheduler_free'], 8)

    def test_probe_uses_all_free_cards_and_reports_mixed_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            def probe(command, **kwargs):
                env = kwargs['env']
                self.assertEqual(env['ZEN_PROBE_GPUS'], '8')
                self.assertEqual(env['ZEN_READY_MIN_FREE_MIB'], '10000')
                state = Path(env['ZEN_STATE_DIR']) / 'accelerator-a.env'
                state.write_text(f'PROBED_AT={int(time.time())}\nFINAL_STATE=COMPLETED\nOCCUPIED=yes\nGPU_READY_COUNT=6\nGPU_PROBED_COUNT=8\nGPU_READY_UUIDS=GPU-a,GPU-b,GPU-c,GPU-d,GPU-e,GPU-f\nJOB_ID=123\n')
                return type('Result', (), {'returncode': 0, 'stdout': 'fixture telemetry'})()
            output = io.StringIO()
            with patch.dict(os.environ, {'ZEN_CACHE_DIR': tmp}), patch('subprocess.check_output', return_value=SNAPSHOT), patch('subprocess.run', side_effect=probe), contextlib.redirect_stdout(output):
                CLI['main'](['--local', '-a', '--remote-root', '/tmp/zen-fixture', '--json', '--min-free-mib', '10000'])
            result = json.loads(output.getvalue())['nodes'][0]
            self.assertEqual(result['status'], 'partial_ready')
            self.assertEqual(result['clean_at_probe'], 6)
            self.assertEqual(len(result['clean_uuids']), 6)

    def test_handoff_registry_failure_does_not_hide_running_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            def registry(remote, root, request):
                if request['op'] == 'claim': return {'state': 'CLAIMED', 'token': 't'}
                if request['op'] == 'handoff': raise OSError('registry temporarily unavailable')
                return {'state': 'OK'}
            output = io.StringIO()
            with patch.dict(os.environ, {'ZEN_CACHE_DIR': tmp}), patch('subprocess.check_output', return_value=SNAPSHOT), contextlib.redirect_stdout(output):
                with patch.dict(CLI['main'].__globals__, peer_call=registry,
                     submit=lambda *args: {'state': 'ADMITTED_NOT_GPU_VERIFIED', 'phase': 'HANDED_OFF', 'job_id': '123'}):
                    rc = CLI['main'](['--local', '--peer-id', 'a', '--registry-root', '/shared/registry',
                                      '--run-sbatch', '/shared/lane', '--remote-root', '/shared/zen'])
            result = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(result['attempts'][0]['job_id'], '123')
            self.assertIn('coordination_warning', result['attempts'][0])

    def test_scheduler_free_without_telemetry_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch.dict(os.environ, {'ZEN_CACHE_DIR': tmp}), patch('subprocess.check_output', return_value=SNAPSHOT), contextlib.redirect_stdout(output):
                CLI['main'](['--local', '--json'])
            result = json.loads(output.getvalue())['nodes'][0]
            self.assertEqual(result['status'], 'unverified')
            self.assertIsNone(result['clean_at_probe'])


if __name__ == '__main__':
    unittest.main()
