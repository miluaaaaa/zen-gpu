"""Durable peer mailboxes; notification, reading and handling are distinct."""
import uuid


def operate(state, request, now):
    messages = state.setdefault('messages', [])
    op, peer = request['op'], request.get('peer_id')
    if op != 'notifications' and peer not in state['peers']:
        raise ValueError('register the sender/recipient first')
    if op == 'send':
        target = request.get('to')
        recipients = [p for p in state['peers'] if p != peer] if target == 'all' else [target]
        if not recipients or any(p not in state['peers'] for p in recipients):
            raise ValueError('unknown or empty recipient list')
        text = request.get('text', '')
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 8000:
            raise ValueError('message must contain 1–8000 UTF-8 bytes')
        key = request.get('request_id')
        old = [m for m in messages if key and m['sender'] == peer and m.get('request_id') == key]
        if old:
            if any(m['text'] != text for m in old) or set(m['recipient'] for m in old) != set(recipients):
                raise ValueError('request ID reused with different content/recipient')
            return {'state': 'QUEUED', 'messages': old, 'duplicate': True}
        reply = request.get('reply_to')
        if reply and not any(m['id'] == reply and peer in (m['sender'], m['recipient']) for m in messages):
            raise ValueError('reply must reference a message visible to this peer')
        if any(sum(m['recipient'] == p and not m.get('acknowledged_at') for m in messages) >= 200 for p in recipients):
            raise ValueError('recipient pending mailbox limit reached')
        new = [{'id': uuid.uuid4().hex, 'sender': peer, 'recipient': p, 'text': text,
                'request_id': key, 'reply_to': reply, 'queued_at': now,
                'delivered_at': None, 'read_at': None, 'acknowledged_at': None} for p in recipients]
        messages.extend(new)
        return {'state': 'QUEUED', 'messages': new, 'duplicate': False}
    if op == 'notifications':
        counts = {}
        for p in state['peers']:
            pending = [m for m in messages if m['recipient'] == p and not m.get('acknowledged_at')]
            counts[p] = {'pending': len(pending), 'unread': sum(not m.get('read_at') for m in pending)}
        return {'state': 'NOTIFICATIONS', 'peers': counts, 'observed_at': now}
    if op in ('inbox', 'outbox'):
        field = 'recipient' if op == 'inbox' else 'sender'
        selected = [m for m in messages if m[field] == peer and (request.get('include_acked') or not m.get('acknowledged_at'))]
        selected = selected[:50]
        for m in selected:
            if op == 'inbox':
                m['delivered_at'] = m.get('delivered_at') or now
                if request.get('mark_read'):
                    m['read_at'] = m.get('read_at') or now
        return {'state': op.upper(), 'messages': selected}
    if op == 'ack':
        m = next((m for m in messages if m['id'] == request.get('message_id')), None)
        if not m or m['recipient'] != peer:
            raise ValueError('only the recipient may acknowledge a message')
        receipt = request.get('receipt', '')
        if not isinstance(receipt, str) or not receipt.strip() or len(receipt.encode()) > 8000:
            raise ValueError('acknowledgement requires a bounded handling receipt')
        if m.get('acknowledged_at') and m['receipt'] != receipt:
            raise ValueError('already acknowledged with a different receipt')
        m.update(delivered_at=m.get('delivered_at') or now, read_at=m.get('read_at') or now,
                 acknowledged_at=m.get('acknowledged_at') or now, receipt=receipt)
        return {'state': 'ACKNOWLEDGED', 'message': m}
    raise ValueError('unknown mailbox operation')
