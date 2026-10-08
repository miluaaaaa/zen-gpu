"""Fair admission for existing launchers; their in-job GPU checks remain mandatory."""
from peer_client import call


def submit(remote, root, peer_id, node, command):
    claim = call(remote, root, {'op': 'claim', 'peer_id': peer_id, 'node': node})
    if claim['state'] != 'CLAIMED':
        return None
    token = claim['token']
    try:
        job = remote(command).strip().split(';')[0]
        if not job.isdigit():
            raise ValueError('unrecognized Slurm job ID')
        call(remote, root, {'op': 'bind', 'peer_id': peer_id, 'token': token, 'job_id': job})
        return job
    except BaseException:
        # If binding fails, do not leave an untracked new workload behind.
        if 'job' in locals() and job.isdigit():
            remote('scancel ' + job)
        call(remote, root, {'op': 'release', 'peer_id': peer_id, 'token': token})
        raise
