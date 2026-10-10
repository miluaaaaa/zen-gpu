"""Coalesce local clients without treating telemetry as workload admission."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import time


def context_key(host, local, root, key, node, minimum):
    return hashlib.sha256(json.dumps([host, local, root, key, node, minimum],
                                    sort_keys=True).encode()).hexdigest()


@contextmanager
def probe_lock(path):
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reusable_busy(state, metadata, context, free, cooldown, max_age, now=None):
    """Only complete, recent zero-ready coverage for identical inputs is reused."""
    if not isinstance(metadata, dict):
        return False
    try:
        age = (time.time() if now is None else now) - int(state.get('PROBED_AT', 0))
        return (cooldown > 0 and 0 <= age <= min(cooldown, max_age)
                and metadata.get('context') == context
                and metadata.get('job_id') == state.get('JOB_ID')
                and state.get('FINAL_STATE') == 'COMPLETED'
                and state.get('OCCUPIED') == 'yes'
                and int(state.get('GPU_READY_COUNT', -1)) == 0
                and int(state.get('GPU_PROBED_COUNT', 0)) >= free)
    except (ValueError, TypeError):
        return False
