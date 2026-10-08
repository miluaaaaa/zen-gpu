"""Submit admission telemetry and a workload in one single-GPU allocation."""
import json
import re
import shlex
import time
import uuid
from pathlib import Path


def submit(remote, node, script, root, minimum, timeout, gate_timeout=60, output=None, export=None, on_submission=None):
    # Copy only directives; execute the original script so its relative paths and
    # environment remain the responsibility of the existing workload launcher.
    source = remote('cat -- ' + shlex.quote(script))
    directives = []
    for line in source.splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            break
        if line.startswith('#SBATCH '):
            options = shlex.split(line[len('#SBATCH '):])
            for option in options:
                key = option.split('=', 1)[0]
                if (key.startswith('--gpus') or key in ('--array', '--het-group',
                        '--ntasks-per-node', '--ntasks-per-gpu', '--ntasks-per-core',
                        '--ntasks-per-socket') or option == '-a' or
                        (option.startswith('-a') and not option.startswith('--'))):
                    raise ValueError('unsupported multi-lane directive: ' + key)
            directives.append(line)
    token = uuid.uuid4().hex
    wrapper = root + '/atomic-' + token + '.sbatch'
    gate = root + '/gate-' + token + '.py'
    evidence = root + '/admission-' + token + '.json'
    remote('mkdir -p -- ' + shlex.quote(root))
    def upload(path, content):
        remote('printf %s ' + shlex.quote(content) + ' > ' + shlex.quote(path))
    upload(gate, (Path(__file__).parent / 'allocation_gate.py').read_text())
    body = '\n'.join(['#!/usr/bin/env bash', *directives, 'set -euo pipefail',
                       'python3 ' + shlex.quote(gate) + ' ' + shlex.quote(evidence) + ' ' + str(minimum),
                       'exec bash ' + shlex.quote(script), ''])
    upload(wrapper, body)
    command = ['sbatch', '--parsable', '--nodes=1', '--ntasks=1', '--gres=gpu:1',
               '--gpu-bind=single:1', '-p', node['partition'], '-w', node['node'], wrapper]
    if output:
        command[1:1] = ['--output=' + output]
    if export:
        command[1:1] = ['--export=' + export]
    job = remote(shlex.join(command)).strip().split(';')[0]
    if not re.fullmatch(r'[0-9]+', job):
        raise ValueError('invalid sbatch job ID')
    result = {'job_id': job, 'node': node['node'], 'admission_file': evidence, 'phase': 'WAITING_ALLOCATION'}
    pending_since = time.monotonic()
    gate_since = None
    handed_off = False
    try:
        if on_submission:
            on_submission(job)
        while True:
            raw = remote('if test -f ' + shlex.quote(evidence) + '; then cat ' + shlex.quote(evidence) + '; fi')
            if raw.strip():
                admission = json.loads(raw)
                result.update(admission)
                result['phase'] = 'CHECKING_ALLOCATION'
                if admission['state'] == 'ADMITTED_NOT_GPU_VERIFIED':
                    handed_off = True
                    result['phase'] = 'HANDED_OFF'
                return result
            queued = remote('squeue -h -j ' + job + ' -o %T').strip()
            now = time.monotonic()
            if queued == 'PENDING':
                if now - pending_since >= timeout:
                    result.update(state='PENDING_TIMEOUT', reason='allocation not obtained within timeout')
                    return result
            elif queued in ('RUNNING', 'CONFIGURING', 'COMPLETING'):
                result['phase'] = 'CHECKING_ALLOCATION'
                if gate_since is None:
                    gate_since = now
                if now - gate_since >= gate_timeout:
                    result.update(state='ADMISSION_TIMEOUT', reason='no admission evidence')
                    return result
            else:
                result['phase'] = 'CHECKING_ALLOCATION'
                result.update(state='ADMISSION_FAILED', reason='job ended without admission evidence')
                return result
            time.sleep(1)
    finally:
        # Retain admitted workload ownership; cancel only this attempt on errors,
        # rejection or timeout. Cached clean UUIDs never authorize admission.
        if not handed_off:
            remote('scancel ' + job)
