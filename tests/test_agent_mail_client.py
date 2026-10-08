import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_mail_client import arguments_for, rpc


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
