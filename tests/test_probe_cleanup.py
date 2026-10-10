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
    def test_term_cancels_owned_probe_even_when_scheduler_query_fails(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            directory = Path(tmp)
            commands = {
                'sbatch': 'echo 123',
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
                proc.send_signal(signal.SIGTERM)
                output = proc.communicate(timeout=5)[0]
                self.assertEqual(proc.returncode, 143, output)
                self.assertEqual((directory / 'cancelled').read_text().strip(), '123')
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
