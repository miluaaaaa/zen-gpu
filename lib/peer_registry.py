"""One login-host registry: locked state, scheduler reconciliation and fair admission."""
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from peer_mailbox import operate as mailbox

ACTIVE = {'RUNNING', 'CONFIGURING', 'COMPLETING'}


def transact(root, request, scheduler, now=None):
    now = time.time() if now is None else now
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'registry.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'registry.json'
        state = json.loads(path.read_text()) if path.exists() else {'peers': {}, 'leases': {}, 'sequence': 0}
        live = {}
        for line in scheduler.splitlines():
            parts = line.split('|')
            if len(parts) == 4:
                job, status, node, gres = parts
                count = re.search(r':(\d+)(?:\([^)]*\))?$', gres)
                live[job] = {'state': status, 'node': node, 'gpus': int(count[1]) if count else 0}
        for peer in state['peers'].values():
            peer['running_gpus'] = sum(live[j]['gpus'] for j in peer['jobs'] if j in live and live[j]['state'] in ACTIVE)
            peer['live_jobs'] = {j: live[j] for j in peer['jobs'] if j in live}
            peer['fresh'] = 0 <= now - peer['heartbeat'] <= 180
        # A bound live job remains known even if its owner stops heartbeating.
        state['leases'] = {k: v for k, v in state['leases'].items()
                           if v.get('job_id') in live or v['expires'] > now}
        op = request['op']
        identity = request.get('peer_id')
        if identity and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', identity):
            raise ValueError('invalid peer identity')
        if op in ('send', 'inbox', 'outbox', 'ack', 'notifications'):
            response = mailbox(state, request, now)
        elif op == 'register':
            peer = state['peers'].setdefault(identity, {'jobs': [], 'last_grant': 0, 'registered': now})
            peer.update(heartbeat=now, fresh=True)
            if not request.get('preserve_demand') or 'ready' not in peer:
                peer.update(project=request.get('project', identity),
                            ready=max(0, int(request.get('ready', 0))), nodes=request.get('nodes', []))
            for field in ('note', 'thread_id'):
                if request.get(field) is not None:
                    peer[field] = request[field]
            incoming = set(request.get('job_ids', []))
            for other_id, other in state['peers'].items():
                if other_id != identity and incoming.intersection(other['jobs']):
                    raise ValueError('job already belongs to another peer')
            peer['jobs'] = sorted(set(peer['jobs']) | incoming)
            peer['running_gpus'] = sum(live[j]['gpus'] for j in peer['jobs'] if j in live and live[j]['state'] in ACTIVE)
            peer['live_jobs'] = {j: live[j] for j in peer['jobs'] if j in live}
            response = {'state': 'REGISTERED', 'peer': peer, 'peer_id': identity}
        elif op == 'peers':
            response = {'state': 'PEERS', 'peers': state['peers'], 'leases': state['leases'], 'observed_at': now}
        elif op in ('claim', 'can-dispatch'):
            peer = state['peers'][identity]
            peer.update(heartbeat=now, fresh=True)
            node = request['node']
            def pending(pid):
                return sum(v['peer_id'] == pid and live.get(v.get('job_id'), {}).get('state') not in ACTIVE
                           for v in state['leases'].values())
            waiting = [(pid, p) for pid, p in state['peers'].items()
                       if p['fresh'] and p['ready'] > 0 and p['running_gpus'] == 0 and not pending(pid)
                       and (not p['nodes'] or node in p['nodes'])]
            waiting.sort(key=lambda item: (item[1]['last_grant'], item[1]['registered'], item[0]))
            priority = waiting[0][0] if waiting else None
            if not peer['ready'] or (peer['nodes'] and node not in peer['nodes']) or (priority and priority != identity):
                response = {'state': 'WAITING_PEER_TURN', 'priority_peer': priority}
            elif op == 'can-dispatch':
                response = {'state': 'ALLOW_DISPATCH'}
            else:
                token = uuid.uuid4().hex
                state['sequence'] += 1
                peer['last_grant'] = state['sequence']
                state['leases'][token] = {'peer_id': identity, 'node': node, 'expires': now + 120}
                response = {'state': 'CLAIMED', 'token': token}
        elif op in ('bind', 'release', 'handoff'):
            lease = state['leases'].get(request['token'])
            if not lease or lease['peer_id'] != identity:
                raise ValueError('missing or foreign admission lease')
            peer = state['peers'][identity]
            peer['heartbeat'] = now
            if op == 'bind':
                lease['job_id'] = request['job_id']
                if request['job_id'] not in peer['jobs']:
                    peer['jobs'].append(request['job_id'])
            elif op == 'handoff':
                peer['ready'] = max(0, peer['ready'] - 1)
                del state['leases'][request['token']]
            else:
                del state['leases'][request['token']]
            response = {'state': op.upper()}
        else:
            raise ValueError('unknown registry operation')
        temporary = root / ('registry-' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(state, indent=2))
        os.replace(temporary, path)
        return response


if __name__ == '__main__':
    print(json.dumps(transact(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3])))
