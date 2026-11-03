import copy
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from research.integration_t11.p2.transport import Outbox, Service, Receiver, ContractError


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name)
        self.paths = [p / 'out', p / 'svc', p / 'recv']
        self.o = Outbox(self.paths[0], True, 2)
        self.s = Service(self.paths[1], True)
        self.r = Receiver(self.paths[2], True)
        self.samples = json.loads(
            Path('research/integration_t11/p1/results/snapshots.json').read_text(encoding='utf-8')
        )['snapshots']

    def tearDown(self):
        self.o.close()
        self.s.close()
        self.r.close()
        self.tmp.cleanup()

    def test_overflow_restart_ack_and_withdrawal(self):
        for i in (3, 4, 5):
            self.o.capture(self.samples[i])
        e = self.o.envelope()
        self.assertEqual(e['head']['snapshot']['sequence'], 6)
        ack = self.s.submit(e, 21)
        self.assertEqual(
            [x['kind'] for x in self.s.dispatch(self.r, 21)], ['local_wet', 'forecast_risk']
        )
        self.o.close()
        self.o = Outbox(self.paths[0], capacity=2)
        self.o.acknowledge(ack)
        self.o.acknowledge(ack)
        self.assertNotEqual(self.o.envelope()['record_id'], ack['record_id'])
        self.assertEqual(self.s.dispatch(self.r, 120), [])

    def test_reorder_duplicate_domains_and_conflict(self):
        self.o.capture(self.samples[3])
        old = self.o.envelope()
        self.o.capture(self.samples[5])
        new = self.o.envelope()
        self.s.submit(new, 21)
        self.s.submit(old, 21)
        self.s.submit(new, 21)
        self.assertEqual(self.s.dispatch(self.r, 21)[0]['kind'], 'local_wet')
        bad = copy.deepcopy(new)
        bad['head']['snapshot']['domain']['device'] = 'other'
        with self.assertRaises(ContractError):
            self.s.submit(bad, 21)
        with self.assertRaises(ContractError):
            self.o.capture(self.samples[3])
        bad = copy.deepcopy(self.samples[5])
        bad['prediction']['state'] = 'changed'
        with self.assertRaises(ContractError):
            self.o.capture(bad)

    def test_multiple_withdrawal_and_atomic_failure(self):
        self.o.capture(self.samples[4])
        self.s.submit(self.o.envelope(), 20)
        self.assertEqual(len(self.s.dispatch(self.r, 20)), 2)
        self.o.capture(self.samples[8])
        e = self.o.envelope()
        self.s.submit(e, 100)
        self.assertEqual(self.s.dispatch(self.r, 100), [])
        bad = copy.deepcopy(e)
        bad['record_id'] = 'wrong'
        with self.assertRaises(ContractError):
            self.s.submit(bad, 100)
        self.s.close()
        self.s = Service(self.paths[1])
        self.assertEqual(self.s.dispatch(self.r, 100), [])

    def test_clock_notice_and_process_restart(self):
        self.o.capture(self.samples[4])
        e = self.o.envelope()
        self.s.submit(e, 20)
        self.s.dispatch(self.r, 20)
        self.assertEqual(len(self.r.notifications), 2)
        self.s.dispatch(self.r, 20)
        self.assertEqual(self.r.notifications, [])
        self.s.close()
        self.r.close()
        code = "from research.integration_t11.p2.transport import Service,Receiver; import sys; s=Service(sys.argv[1]); r=Receiver(sys.argv[2]); assert len(s.dispatch(r,21))==2; assert r.notifications==[]; s.close(); r.close()"
        subprocess.run(
            [sys.executable, '-B', '-c', code, str(self.paths[1]), str(self.paths[2])], check=True
        )
        self.s = Service(self.paths[1])
        self.r = Receiver(self.paths[2])
        with self.assertRaises(ContractError):
            self.s.dispatch(self.r, 20)
        self.assertEqual(len(self.s.dispatch(self.r, 21)), 2)

    def test_fractional_prediction_and_future_record(self):
        snap = copy.deepcopy(self.samples[4])
        intent = snap['intents'][-1]
        intent['kind'] = 'prediction_unresolved'
        intent['evidence_s'] = 19.5
        intent['expires_s'] = 99.5
        self.o.capture(snap)
        e = self.o.envelope()
        self.s.submit(e, 20)
        self.assertEqual(len(self.s.dispatch(self.r, 20)), 2)
        with self.assertRaises(ContractError):
            self.s.submit(e, 19)

    def test_eviction_count_and_ack_validation(self):
        self.assertEqual(self.o.dropped_history_count, 0)
        for i in (3, 4, 5, 6):
            self.o.capture(self.samples[i])
        self.assertEqual(self.o.dropped_history_count, 2)
        envelope = self.o.envelope()
        ack = self.s.submit(envelope, 22)
        for bad in (
            {},
            {**ack, 'extra': 1},
            {**ack, 'record_id': 5},
            {**ack, 'record_id': 'a' * 63},
            {**ack, 'record_id': 'G' * 64},
            {**ack, 'device': None},
        ):
            with self.assertRaises(ContractError):
                self.o.acknowledge(bad)
        self.assertEqual(self.o.envelope()['record_id'], envelope['record_id'])
        self.o.acknowledge(ack)
        self.assertEqual(self.o.dropped_history_count, 2)
        self.o.close()
        code = (
            "from research.integration_t11.p2.transport import Outbox; import sys; "
            "o=Outbox(sys.argv[1],capacity=2); "
            "assert o.dropped_history_count==2; o.close()"
        )
        subprocess.run([sys.executable, '-B', '-c', code, str(self.paths[0])], check=True)
        self.o = Outbox(self.paths[0], capacity=2)
        self.o.capture(self.samples[7])
        self.o.capture(self.samples[8])
        self.assertEqual(self.o.dropped_history_count, 3)

    def test_cross_role_and_missing(self):
        with self.assertRaises(ContractError):
            Service(self.paths[0])
        with self.assertRaises(ContractError):
            Outbox(Path(self.tmp.name) / 'missing')


if __name__ == '__main__':
    unittest.main()
