import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from peer_registry import transact


def register(root, peer, jobs=(), nodes=(), ready=1, now=10):
    return transact(root, {'op': 'register', 'peer_id': peer, 'ready': ready,
                          'nodes': list(nodes), 'job_ids': list(jobs)}, '', now)


def contender(root, peer, output):
    output.put(transact(root, {'op': 'claim', 'peer_id': peer, 'node': 'gpu-a'}, '', 20)['state'])


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.root = self.tmp.name
    def tearDown(self):self.tmp.cleanup()
    def claim(self, peer, node='gpu-a', scheduler='', now=20):
        return transact(self.root, {'op': 'claim', 'peer_id': peer, 'node': node}, scheduler, now)
    def test_waiting_peer_precedes_peer_with_running_gpu(self):
        register(self.root, 'busy', ['1']);register(self.root, 'waiting')
        result=self.claim('busy', scheduler='1|RUNNING|gpu-a|gpu:1')
        self.assertEqual(result['priority_peer'], 'waiting')
        self.assertEqual(result['state'], 'WAITING_PEER_TURN')
    def test_pending_is_not_running_and_lease_coordinates_concurrent_claims(self):
        register(self.root, 'a');register(self.root, 'b')
        first=self.claim('a');self.assertEqual(first['state'], 'CLAIMED')
        self.assertEqual(self.claim('b')['state'], 'CLAIMED')
        peers=transact(self.root, {'op': 'peers'}, '', 20)['peers']
        self.assertTrue(all(p['running_gpus']==0 for p in peers.values()))
    def test_incompatible_peer_does_not_block_suitable_gpu(self):
        register(self.root, 'a', nodes=['gpu-b']);register(self.root, 'b', nodes=['gpu-a'])
        self.assertEqual(self.claim('b')['state'], 'CLAIMED')
    def test_stale_waiter_does_not_block_live_owner(self):
        register(self.root, 'old', now=0);register(self.root, 'new', now=190)
        self.assertEqual(self.claim('new', now=200)['state'], 'CLAIMED')
    def test_expired_unbound_lease_is_removed(self):
        register(self.root, 'a');token=self.claim('a')['token']
        self.assertNotIn(token, transact(self.root, {'op':'peers'}, '', 200)['leases'])
    def test_running_job_remains_visible_after_heartbeat_expires(self):
        register(self.root, 'a', ['1'])
        p=transact(self.root, {'op':'peers'}, '1|RUNNING|gpu-a|gpu:1', 200)['peers']['a']
        self.assertFalse(p['fresh']);self.assertEqual(p['running_gpus'],1)
    def test_no_fixed_gpu_cap_when_every_peer_has_capacity(self):
        register(self.root, 'a',['1']);register(self.root, 'b',['2'])
        scheduler='1|RUNNING|gpu-a|gpu:1\n2|RUNNING|gpu-b|gpu:1'
        for _ in range(10):self.assertEqual(self.claim('a', scheduler=scheduler)['state'],'CLAIMED')
    def test_foreign_lease_cannot_be_released(self):
        register(self.root,'a');register(self.root,'b');token=self.claim('a')['token']
        with self.assertRaises(ValueError):
            transact(self.root, {'op':'release','peer_id':'b','token':token}, '',20)
    def test_register_ready_zero_does_not_compete(self):
        register(self.root,'a',ready=0);register(self.root,'b')
        self.assertEqual(self.claim('b')['state'],'CLAIMED')
    def test_launch_preserves_registered_compatibility_and_demand(self):
        register(self.root, 'a', nodes=['gpu-a','gpu-b'], ready=4)
        transact(self.root, {'op':'register','peer_id':'a','ready':1,'nodes':['gpu-a'],
                             'preserve_demand':True}, '',20)
        peer=transact(self.root,{'op':'peers'},'',20)['peers']['a']
        self.assertEqual(peer['nodes'],['gpu-a','gpu-b']);self.assertEqual(peer['ready'],4)
    def test_own_incompatible_node_is_not_claimed(self):
        register(self.root,'a',nodes=['gpu-b'])
        self.assertEqual(self.claim('a')['state'],'WAITING_PEER_TURN')
    def test_duplicate_job_ownership_is_rejected(self):
        register(self.root,'a',['1'])
        with self.assertRaises(ValueError):register(self.root,'b',['1'])
    def test_advisory_check_creates_no_reservation(self):
        register(self.root,'a')
        result=transact(self.root,{'op':'can-dispatch','peer_id':'a','node':'gpu-a'},'',20)
        self.assertEqual(result['state'],'ALLOW_DISPATCH')
        self.assertEqual(transact(self.root,{'op':'peers'},'',20)['leases'],{})
    def test_concurrent_writes_preserve_every_claim(self):
        peers=['p'+str(i) for i in range(4)]
        for peer in peers:register(self.root,peer)
        output=multiprocessing.Queue()
        processes=[multiprocessing.Process(target=contender,args=(self.root,peer,output)) for peer in peers]
        for p in processes:p.start()
        for p in processes:p.join(10);self.assertEqual(p.exitcode,0)
        results=[output.get(timeout=2) for _ in processes]
        state=transact(self.root,{'op':'peers'},'',20)
        self.assertEqual(len(state['leases']),results.count('CLAIMED'))
        self.assertEqual(len(state['peers']),4)
