"""Bounded retries based on progress, never on error-message matching."""
import time


def retry_policy(attempt, delay):
    phase = attempt.get('phase')
    if phase == 'WAITING_ALLOCATION':
        return {'retry': True, 'delay_seconds': 0, 'action': 'TRY_NEXT_CANDIDATE'}
    if phase == 'CHECKING_ALLOCATION':
        return {'retry': True, 'delay_seconds': delay, 'action': 'BACKOFF_CANDIDATE'}
    # Handoff and unknown phases cannot trigger automatic workload restarts.
    return {'retry': False, 'delay_seconds': 0, 'action': 'OWNER_MONITORING'}


def launch_candidates(get_nodes, launch, rounds=1, delay=10, sleep=time.sleep, clock=time.monotonic):
    attempts = []
    cooldown = {}
    for round_index in range(rounds):
        if round_index:
            sleep(delay)
        for node in get_nodes():
            name = node['node']
            if cooldown.get(name, 0) > clock():
                continue
            if not node['partition'] or not node['scheduler_free']:
                continue
            if any(flag in node['state'] for flag in ('DOWN', 'DRAIN', 'NOT_RESPOND', 'FAIL', 'MAINT')):
                continue
            result = launch(node)
            policy = retry_policy(result, delay)
            result.update(retry_policy=policy, attempt_round=round_index + 1)
            attempts.append(result)
            if result.get('phase') == 'HANDED_OFF':
                return {'state': result['state'], 'attempts': attempts}
            if not policy['retry']:
                return {'state': 'STOPPED_REQUIRES_DIAGNOSTIC', 'attempts': attempts}
            cooldown[name] = clock() + policy['delay_seconds']
    return {'state': 'NO_ADMITTED_GPU', 'attempts': attempts}
