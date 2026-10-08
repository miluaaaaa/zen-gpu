"""Fail-closed single-GPU admission inside the workload's Slurm allocation."""
import json
import os
from pathlib import Path
import subprocess
import sys


def check(env, query, minimum):
    visible = env.get('CUDA_VISIBLE_DEVICES', '').split(',')
    allocated = env.get('SLURM_JOB_GPUS', '').split(',')
    if len(visible) != 1 or not visible[0] or len(allocated) != 1 or not allocated[0]:
        raise ValueError('exactly one allocated and visible GPU is required')
    # SLURM_JOB_GPUS uses global GPU IDs; CUDA indices may be remapped by cgroups.
    card = allocated[0]
    raw = query(['nvidia-smi', '-i', card,
                 '--query-gpu=uuid,memory.used,memory.total,utilization.gpu',
                 '--format=csv,noheader,nounits'])
    rows = [line for line in raw.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError('allocated GPU telemetry must resolve to one card')
    uuid, used, total, util = [part.strip() for part in rows[0].split(',')]
    if not uuid.startswith('GPU-'):
        raise ValueError('unsupported GPU identity')
    processes = query(['nvidia-smi', '-i', card,
                       '--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader,nounits'])
    for line in processes.splitlines():
        if not line.strip() or 'No running processes found' in line:
            continue
        fields = [part.strip() for part in line.split(',')]
        if len(fields) != 2 or fields[0] != uuid or not fields[1].isdigit():
            raise ValueError('unrecognized process telemetry')
        raise ValueError('allocated GPU occupied before model load')
    if not (0 <= int(used) <= int(total)) or int(total) <= 0 or int(util) != 0 or int(total) - int(used) < minimum:
        raise ValueError('allocated GPU occupied before model load')
    return uuid


def main():
    path = Path(sys.argv[1])
    try:
        uuid = check(os.environ, lambda argv: subprocess.check_output(argv, text=True), int(sys.argv[2]))
        state = {'state': 'ADMITTED_NOT_GPU_VERIFIED', 'gpu_uuid': uuid,
                 'job_id': os.environ['SLURM_JOB_ID']}
        rc = 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        state = {'state': 'REJECTED', 'reason': str(exc)}
        rc = 78
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.replace(path)
    return rc


if __name__ == '__main__':
    sys.exit(main())
