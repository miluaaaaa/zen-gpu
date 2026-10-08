"""Small authenticated client for an existing MCP Agent Mail HTTP service."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlparse
import uuid
from datetime import datetime, timezone


COORDINATION_STATES = ('REQUEST', 'ACCEPT', 'REJECT', 'DEFER', 'CONFIRM', 'EXECUTED', 'BLOCKED')


def coordination_body(agreement, state, expires_at, text, evidence=None):
    """Encode a peer's decision, not a resource reservation or execution command."""
    if not agreement or not agreement.strip() or state not in COORDINATION_STATES:
        raise ValueError('An agreement ID and a valid decision state are required')
    expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('expires-at must have a timezone and be in the future')
    if not text or not text.strip():
        raise ValueError('Describe the concrete action, conditions or blocker')
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError('Evidence must be a JSON object')
    if state == 'EXECUTED' and (not isinstance(evidence, dict) or not evidence):
        raise ValueError('EXECUTED requires a nonempty evidence JSON object')
    return json.dumps({'protocol': 'zen-peer-negotiation/v1', 'agreement': agreement,
        'state': state, 'expires_at': expiry.isoformat(), 'detail': text,
        'evidence': evidence or {}}, ensure_ascii=False)


def rpc(config, name, arguments):
    url = config['url']
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('This adapter requires a loopback HTTP Agent Mail service')
    request = urllib.request.Request(url, json.dumps({
        'jsonrpc': '2.0', 'id': uuid.uuid4().hex, 'method': 'tools/call',
        'params': {'name': name, 'arguments': arguments},
    }).encode(), headers={'Authorization': 'Bearer ' + config['bearer_token'],
                         'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream',
                         'User-Agent': 'OpenAI File Downloader, XaiImageApiFetch/1.0'})
    # Local services must not inherit an outbound HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as response:
        data = json.load(response)
    if data.get('error'):
        raise RuntimeError('Agent Mail RPC failed: ' + str(data['error'].get('code')))
    result = data['result']
    if result.get('isError'):
        raise RuntimeError('Agent Mail tool rejected the request; inspect service logs')
    if 'structuredContent' in result:
        structured = result['structuredContent']
        return structured.get('result', structured) if isinstance(structured, dict) else structured
    texts = [item['text'] for item in result.get('content', []) if item.get('type') == 'text']
    if not texts:
        raise RuntimeError('Agent Mail returned no JSON tool result')
    return json.loads(texts[0])


def arguments_for(config, command, peer, **values):
    identity = config['peers'][peer]
    common = {'project_key': config['project_key']}
    if command == 'inbox':
        return 'fetch_inbox', dict(common, agent_name=identity['name'],
            registration_token=identity['registration_token'], include_bodies=True,
            unread_only=values.get('unread_only', False), limit=100)
    if command == 'send':
        return 'send_message', dict(common, sender_name=identity['name'],
            sender_token=identity['registration_token'],
            to=[config['peers'][p]['name'] for p in values['to']],
            subject=values['subject'], body_md=values['text'],
            thread_id=values.get('thread', 'gpu-coordination'), ack_required=True)
    if command == 'reply':
        return 'reply_message', dict(common, sender_name=identity['name'],
            sender_token=identity['registration_token'], message_id=values['message_id'],
            body_md=values['text'])
    if command in ('ack', 'read'):
        return ('acknowledge_message' if command == 'ack' else 'mark_message_read'), dict(
            common, agent_name=identity['name'], registration_token=identity['registration_token'],
            message_id=values['message_id'])
    raise ValueError('Unknown mail operation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['peers', 'inbox', 'send', 'reply', 'read', 'ack', 'notifications', 'negotiate'])
    parser.add_argument('--config', default=os.environ.get('ZEN_AGENT_MAIL_CONFIG',
        str(Path.home() / '.config/zen-gpu/agent-mail.json')))
    parser.add_argument('--peer-id')
    parser.add_argument('--to', action='append')
    parser.add_argument('--subject', default='GPU coordination')
    parser.add_argument('--text')
    parser.add_argument('--message-file')
    parser.add_argument('--thread', default='gpu-coordination')
    parser.add_argument('--message-id', type=int)
    parser.add_argument('--unread-only', action='store_true')
    parser.add_argument('--agreement')
    parser.add_argument('--state', choices=COORDINATION_STATES)
    parser.add_argument('--expires-at')
    parser.add_argument('--evidence-file', help='JSON object with checkable execution evidence')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.command == 'peers':
        output = {p: {'name': v['name'], 'thread_id': v.get('thread_id'), 'task': v.get('task')}
                  for p, v in config['peers'].items()}
    elif args.command == 'notifications':
        output = {}
        for peer in config['peers']:
            tool, values = arguments_for(config, 'inbox', peer, unread_only=True)
            messages = rpc(config, tool, values)
            output[peer] = {'pending': len(messages), 'unread': len(messages)}
    else:
        if args.peer_id not in config['peers']:
            parser.error('--peer-id must name a configured peer')
        if args.command in ('send', 'reply', 'negotiate'):
            if bool(args.text) == bool(args.message_file):
                parser.error('Specify exactly one of --text or --message-file')
            if args.message_file:
                args.text = Path(args.message_file).read_text()
            if args.command in ('send', 'negotiate') and (not args.to or any(p not in config['peers'] for p in args.to)):
                parser.error('--to must name configured recipients')
        if args.command == 'negotiate':
            if not args.agreement or not args.state or not args.expires_at:
                parser.error('negotiate requires --agreement, --state and --expires-at')
            try:
                evidence = json.loads(Path(args.evidence_file).read_text()) if args.evidence_file else None
                args.text = coordination_body(args.agreement, args.state, args.expires_at, args.text, evidence)
            except (ValueError, OSError) as error:
                parser.error(str(error))
            args.thread = 'agreement-' + args.agreement
            args.subject = args.state + ': ' + args.agreement
            args.command = 'send'
        if args.command in ('reply', 'read', 'ack') and args.message_id is None:
            parser.error('--message-id is required')
        tool, values = arguments_for(config, args.command, args.peer_id,
            to=args.to, subject=args.subject, text=args.text, thread=args.thread,
            message_id=args.message_id, unread_only=args.unread_only)
        output = rpc(config, tool, values)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
