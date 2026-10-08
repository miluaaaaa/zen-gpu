import sys
from pathlib import Path
import unittest
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_mail_client import arguments_for, rpc, coordination_body


class AgentMailClientTests(unittest.TestCase):
    def setUp(self):
        self.config = {'project_key': '/coord', 'peers': {
            'a': {'name': 'Alpha', 'registration_token': 'a-token'},
            'b': {'name': 'Beta', 'registration_token': 'b-token'}}}

    def test_sender_auth_and_recipient_mapping(self):
        name, args = arguments_for(self.config, 'send', 'a', to=['b'], subject='GPU', text='request')
        self.assertEqual(name, 'send_message')
        self.assertEqual(args['sender_token'], 'a-token')
        self.assertEqual(args['to'], ['Beta'])
        self.assertTrue(args['ack_required'])

    def test_fetch_does_not_mark_read_or_ack(self):
        name, args = arguments_for(self.config, 'inbox', 'b')
        self.assertEqual(name, 'fetch_inbox')
        self.assertEqual(args['registration_token'], 'b-token')
        self.assertNotIn('mark_read', args)

    def test_reply_binds_original_message(self):
        name, args = arguments_for(self.config, 'reply', 'b', message_id=7, text='accepted')
        self.assertEqual(name, 'reply_message')
        self.assertEqual(args['message_id'], 7)
        self.assertEqual(args['sender_token'], 'b-token')

    def test_refuse_remote_endpoint_before_credentials_sent(self):
        with self.assertRaises(ValueError):
            rpc({'url': 'http://example.com/api/', 'bearer_token': 'secret'}, 'health_check', {})

    def test_negotiation_has_explicit_conditions_and_expiry(self):
        event = json.loads(coordination_body('handoff-1', 'DEFER', '2099-01-01T00:00:00Z',
            'Revisit when lane 4 completes; no safe checkpoint yet'))
        self.assertEqual(event['state'], 'DEFER')
        self.assertEqual(event['agreement'], 'handoff-1')
        self.assertIn('+00:00', event['expires_at'])

    def test_expired_or_timezone_free_decisions_rejected(self):
        for expiry in ('2000-01-01T00:00:00Z', '2099-01-01T00:00:00', 'invalid'):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                coordination_body('handoff-1', 'ACCEPT', expiry, 'After completion')

    def test_execution_report_requires_evidence_object(self):
        for evidence in (None, {}, ['allocated']):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                coordination_body('handoff-1', 'EXECUTED', '2099-01-01T00:00:00Z',
                    'Started inference', evidence)
        event = json.loads(coordination_body('handoff-1', 'EXECUTED', '2099-01-01T00:00:00Z',
            'Started inference', {'job': 123, 'gpu_evidence_path': '/experiment/gpu_evidence.json'}))
        self.assertEqual(event['evidence']['job'], 123)
