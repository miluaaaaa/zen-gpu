import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from peer_registry import transact
class MailboxTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=self.tmp.name
  for p in ('a','b','c'):self.call('register',p,ready=1)
 def tearDown(self):self.tmp.cleanup()
 def call(self,op,peer=None,**kw):return transact(self.root,{'op':op,'peer_id':peer,**kw},'',now=10)
 def send(self,**kw):return self.call('send','a',to='b',text='Need a lane when available',**kw)['messages'][0]
 def test_delivery_read_and_handling_are_separate(self):
  m=self.send();self.assertIsNone(m['read_at'])
  m=self.call('inbox','b')['messages'][0];self.assertEqual(m['delivered_at'],10);self.assertIsNone(m['read_at'])
  m=self.call('inbox','b',mark_read=True)['messages'][0];self.assertEqual(m['read_at'],10);self.assertIsNone(m['acknowledged_at'])
  self.call('ack','b',message_id=m['id'],receipt='Will yield next released lane')
  self.assertEqual(self.call('inbox','b')['messages'],[])
  self.assertEqual(self.call('outbox','a',include_acked=True)['messages'][0]['receipt'],'Will yield next released lane')
 def test_other_peer_cannot_ack(self):
  m=self.send()
  with self.assertRaises(ValueError):self.call('ack','c',message_id=m['id'],receipt='done')
 def test_idempotent_send(self):
  self.send(request_id='same');self.send(request_id='same')
  self.assertEqual(len(self.call('inbox','b')['messages']),1)
  with self.assertRaises(ValueError):self.call('send','a',to='b',text='different',request_id='same')
 def test_broadcast_excludes_sender(self):
  result=self.call('send','a',to='all',text='status')
  self.assertEqual({m['recipient'] for m in result['messages']},{'b','c'})
 def test_notification_does_not_mark_read(self):
  self.send();self.assertEqual(self.call('notifications')['peers']['b'],{'pending':1,'unread':1})
  self.assertIsNone(self.call('outbox','a')['messages'][0]['delivered_at'])
 def test_invalid_body_and_unknown_recipient(self):
  for to,text in [('missing','x'),('b',''),('b','x'*8001)]:
   with self.assertRaises(ValueError):self.call('send','a',to=to,text=text)
 def test_receipt_is_required(self):
  m=self.send()
  with self.assertRaises(ValueError):self.call('ack','b',message_id=m['id'],receipt='')
 def test_reply_links_existing_message(self):
  m=self.send();reply=self.call('send','b',to='a',text='Acknowledged plan',reply_to=m['id'])
  self.assertEqual(reply['messages'][0]['reply_to'],m['id'])
