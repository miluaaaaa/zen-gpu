"""Run the actual shell probe against a synthetic Slurm command set."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProbeCleanupTests(unittest.TestCase):
    def test_named_probe_reads_the_corresponding_slurm_output(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            directory = Path(tmp)
            commands = {
                'sbatch': '''mkdir -p "$ZEN_REMOTE_ROOT/logs"
printf 'MACHINE_PROBED_COUNT|1\nMACHINE_READY_COUNT|1\nMACHINE_READY_UUIDS|GPU-fixture\nMACHINE_PROBED_UUIDS|GPU-fixture\nMACHINE_OCCUPIED|no\n' > "$ZEN_REMOTE_ROOT/logs/$ZEN_PROBE_JOB_NAME-123.out"
touch "$ZEN_REMOTE_ROOT/logs/$ZEN_PROBE_JOB_NAME-123.err"
echo 123''',
                'squeue': 'exit 0',
                'sacct': 'echo COMPLETED',
                'scancel': 'exit 0',
                'scontrol': 'echo "NodeName=fixture State=IDLE CfgTRES=gres/gpu=1 AllocTRES="',
            }
            for name, body in commands.items():
                command = directory / name
                command.write_text('#!/bin/bash\n' + body + '\n')
                command.chmod(0o755)
            env = dict(os.environ, PATH=str(directory) + ':' + os.environ['PATH'],
                       ZEN_LOCAL='1', ZEN_PARTITION='fixture', ZEN_PROBE_GPUS='1',
                       ZEN_REMOTE_ROOT=tmp + '/remote', ZEN_CACHE_DIR=tmp + '/cache',
                       ZEN_STATE_DIR=tmp + '/cache/state',
                       ZEN_PROBE_JOB_NAME='shunongLLM-8E6F')
            result = subprocess.run(['bash', str(ROOT / 'lib/probe_via_job.sh'), 'fixture'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = (directory / 'cache/state/fixture.env').read_text()
            self.assertIn('GPU_PROBED_COUNT=1', state)
            self.assertIn('GPU_READY_COUNT=1', state)

    def test_term_cancels_owned_probe_even_when_scheduler_query_fails(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            directory = Path(tmp)
            commands = {
                'sbatch': 'cat "${@: -1}" > "$FIXTURE/submitted.sbatch"; echo 123',
                'squeue': 'test ! -f "$FIXTURE/query-fails" || exit 1; echo PENDING',
                'scancel': 'echo "$1" >> "$FIXTURE/cancelled"',
            }
            for name, body in commands.items():
                path = directory / name
                path.write_text('#!/bin/bash\n' + body + '\n')
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(directory) + ':' + os.environ['PATH'],
                       FIXTURE=tmp, ZEN_LOCAL='1', ZEN_PARTITION='fixture',
                       ZEN_REMOTE_ROOT=tmp + '/remote', ZEN_CACHE_DIR=tmp + '/cache',
                       ZEN_PROBE_TRACK_DIR=tmp + '/tracking', ZEN_PROBE_POLL_SECONDS='0.1',
                       ZEN_PROBE_JOB_NAME='shunongLLM-8E6F',
                       ZEN_PROBE_PENDING_TIMEOUT_SECONDS='30')
            proc = subprocess.Popen(['bash', str(ROOT / 'lib/probe_via_job.sh'), 'fixture'],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                deadline = time.monotonic() + 5
                while not (directory / 'tracking/probe_123.env').exists():
                    if proc.poll() is not None or time.monotonic() > deadline:
                        self.fail('probe did not enter polling: ' + proc.communicate(timeout=1)[0])
                    time.sleep(0.02)
                (directory / 'query-fails').touch()
                self.assertIn('#SBATCH -J shunongLLM-8E6F', (directory / 'submitted.sbatch').read_text())
                proc.send_signal(signal.SIGTERM)
                output = proc.communicate(timeout=5)[0]
                self.assertEqual(proc.returncode, 143, output)
                self.assertEqual((directory / 'cancelled').read_text().strip(), '123')
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
