"""Pure helpers for Zen's elastic lane reconciler."""

ACTIVE_STATES = frozenset({
    'PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED',
    'RESIZING', 'SIGNALING', 'STAGE_OUT', 'REQUEUED', 'SPECIAL_EXIT',
})
TERMINAL_STATES = frozenset({
    'BOOT_FAIL', 'CANCELLED', 'COMPLETED', 'DEADLINE', 'FAILED',
    'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED', 'REVOKED', 'STOPPED',
    'TIMEOUT', 'PREEMPTED', 'OOM',
})


def parse_job_states(text):
    """Parse ``job_id|state`` rows emitted by squeue/sacct."""
    result = {}
    for line in text.splitlines():
        parts = line.strip().split('|', 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        result[parts[0]] = parts[1].split()[0].upper()
    return result


def active_job_ids(states):
    """Return jobs which still reserve a lane, including pending jobs."""
    return sorted(job_id for job_id, state in states.items() if state in ACTIVE_STATES)


def terminal_job_ids(states):
    """Return jobs which no longer reserve a lane."""
    return sorted(job_id for job_id, state in states.items() if state in TERMINAL_STATES)


def target_from_arg(value, scheduler_free):
    """Resolve an explicit target or the current scheduler-free GPU total."""
    if value == 'auto':
        return max(0, sum(max(0, int(item.get('scheduler_free', 0))) for item in scheduler_free))
    target = int(value)
    if target < 1:
        raise ValueError('--target-gpus must be a positive integer or auto')
    return target
