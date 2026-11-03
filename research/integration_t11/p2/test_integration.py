"""Coordinator regressions for source-bound host traces and durable delivery."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .power import Battery
from .replay import RESULT, ROOT, scenario
from .runner import Rig, reopen_after_boot
from .transport import Outbox, Receiver, Service


def rows(case, action):
    return [r for r in case['trace'] if r['action'] == action]


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c['case']: c for c in json.loads(RESULT.read_bytes())['cases']}

    def test_all_energy_balances(self):
        for name, case in self.cases.items():
            e = case['energy']
            with self.subTest(name=name):
                self.assertAlmostEqual(
                    e['initial_j'] + e['charge_battery_j'],
                    sum(
                        e[k]
                        for k in (
                            'stored_j',
                            'curtailed_j',
                            'baseline_j',
                            'measurement_j',
                            'radio_j',
                            'external_j',
                        )
                    ),
                    places=7,
                )
                self.assertAlmostEqual(
                    e['baseline_j'] + e['unserved_baseline_j'], 0.0002 * 189 / 0.85
                )
                self.assertFalse(case['collection_authorized'])
                self.assertIsNone(case['phone_delivery_s'])
                self.assertGreaterEqual(e['stored_j'], e['reserve_j'])

    def test_full_charge_cannot_pay_future_valve_edge(self):
        tiny = Battery(initial_j=0, capacity_ah=1e-16)
        self.assertFalse(tiny.spend(1e-12))
        self.assertEqual(tiny.stored_j, 0)
        b = Battery()
        b.charge(1, 0.75)
        self.assertTrue(b.start_measurement())
        b.advance(30)
        self.assertAlmostEqual(b.stored_j, b.capacity_j - 0.05 / 0.85)
        self.assertTrue(b.cancel_measurement())
        self.assertAlmostEqual(b.stored_j, b.capacity_j - 0.10 / 0.85)

    def test_wet_priority_cost_latency_and_causal_prefix(self):
        connected, delayed = (self.cases[k] for k in ('wet_connected', 'wet_reconnect'))
        # The producer changes the future channel. Before observing failure the consumer agrees.
        self.assertEqual(
            [r for r in connected['trace'] if r['t'] <= 62],
            [r for r in delayed['trace'] if r['t'] <= 62],
        )
        for c, received in [(connected, 68), (delayed, 148)]:
            self.assertEqual(
                c['wet_latency'],
                dict(
                    synthetic_water_onset_s=61,
                    local_observed_s=62,
                    first_queued_s=62,
                    first_radio_start_s=62,
                    first_server_saved_s=received,
                    first_simulated_receiver_s=received,
                    phone_received_s=None,
                ),
            )
            self.assertEqual([r['t'] for r in rows(c, 'measurement_cancelled')], [62])
            self.assertAlmostEqual(
                c['energy']['measurement_j'], (0.002 * 35 + 0.2 * 5 + 0.10) / 0.85
            )
        self.assertEqual(
            [(r['t'], r['retry_at']) for r in rows(delayed, 'radio_timeout')], [(82, 142)]
        )
        self.assertAlmostEqual(self.cases['normal_cycle']['energy']['measurement_j'], 4.38 / 0.85)
        self.assertEqual(
            rows(self.cases['normal_cycle'], 'measurement_budget_complete')[0]['t'], 167
        )

    def test_supersession_expiry_and_lost_ack(self):
        for name in ('change_offline', 'boot_offline'):
            c = self.cases[name]
            by_time = {r['t']: [i['kind'] for i in r['result']] for r in rows(c, 'receiver_query')}
            self.assertEqual(by_time[74], ['prediction_conditional'])
            self.assertEqual(by_time[101], [])
            self.assertIsNone(c['wet_latency']['first_simulated_receiver_s'])
            self.assertEqual(
                next(r['result'] for r in rows(c, 'simulated_receiver') if r['t'] == 148), []
            )
        c = self.cases['ack_lost']
        deliveries = [
            r
            for r in rows(c, 'simulated_receiver')
            if any(i['kind'] == 'local_wet' for i in r['result'])
        ]
        self.assertEqual([r['t'] for r in deliveries], [68, 148])
        self.assertEqual([len(r['newly_notified']) for r in deliveries], [1, 0])
        self.assertFalse(any(r['t'] == 68 for r in rows(c, 'device_ack')))
        self.assertEqual(rows(c, 'receiver_query')[-1]['result'], [])

    def test_manual_charge_and_missing_samples(self):
        off, on = (self.cases[k] for k in ('no_charge', 'manual_charge'))
        self.assertFalse(rows(off, 'radio_start'))
        self.assertFalse(rows(off, 'pc_analysis'))
        self.assertAlmostEqual(on['energy']['charge_input_j'], 35)
        self.assertAlmostEqual(on['energy']['charge_battery_j'], 26.25)
        self.assertEqual(rows(on, 'radio_start')[0]['t'], 60)
        self.assertFalse(rows(on, 'pc_analysis'))
        c = self.cases['missing_packet']
        self.assertEqual([r['t'] for r in rows(c, 'analysis_missing_sample')], [26])
        self.assertEqual(
            [r['prediction'] for r in rows(c, 'state_saved') if r['kind'] == 'prediction'],
            ['unresolved'],
        )

    def test_exhausted_retry_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(Path(tmp))
            try:
                _, inputs = scenario('wet_connected')
                rig.step(0, [*inputs[0], ('link', False)])
                for t in range(1, 5501):
                    rig.step(t, [('link', True)] if t == 5100 else ())
                self.assertEqual(
                    [r['t'] for r in rig.trace if r['action'] == 'radio_start'],
                    [0, 80, 400, 1320, 4940],
                )
                self.assertEqual(rig.failures, 5)
                self.assertIsNone(
                    [r for r in rig.trace if r['action'] == 'radio_timeout'][-1]['retry_at']
                )
                self.assertAlmostEqual(rig.battery.ledger()['radio_j'], 5 * 0.6 * 20 / 0.85)
            finally:
                rig.close()

    def test_expired_packet_at_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(Path(tmp))
            try:
                _, inputs = scenario('normal_cycle')
                job = next(p for kind, p in inputs[20] if kind == 'predict')
                job['request']['policy']['max_sample_age_s'] = 15
                for t in range(27):
                    rig.step(t, inputs.get(t, ()))
                self.assertTrue(any(r['action'] == 'analysis_obsolete' for r in rig.trace))
                self.assertIsNone(rig.controller.prediction)
                self.assertEqual(rig.receiver.notifications, [])
            finally:
                rig.close()

    def test_storage_failure_does_not_advance_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            rig = Rig(Path(tmp))
            try:
                _, inputs = scenario('normal_cycle')
                kind, payload = inputs[0][0]
                with patch.object(rig.device, 'capture', side_effect=OSError('write failed')):
                    with self.assertRaises(OSError):
                        rig.emit(kind, payload)
                self.assertEqual(rig.controller.seq, 0)
                self.assertIsNone(rig.device.envelope())
                rig.emit(kind, payload)
                self.assertEqual(rig.device.envelope()['head']['snapshot']['sequence'], 1)
            finally:
                rig.close()

    def test_process_death_then_boot_before_upload(self):
        snapshots = json.loads(
            (ROOT / 'research/integration_t11/p1/results/snapshots.json').read_bytes()
        )['snapshots']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'out.db'
            transfer = Path(tmp) / 'snapshot.json'
            out = Outbox(path, create=True, capacity=1)
            out.capture(snapshots[4])
            old_envelope = out.envelope()
            out.close()
            transfer.write_text(json.dumps(snapshots[5]), encoding='utf-8')
            for point, code in [('during', 73), ('after', 74)]:
                run = subprocess.run(
                    [sys.executable, '-B', '-m', __spec__.name, point, str(path), str(transfer)],
                    cwd=ROOT,
                    timeout=30,
                )
                self.assertEqual(run.returncode, code)
                out = Outbox(path, capacity=1)
                try:
                    self.assertEqual(out.dropped_history_count, int(point == 'after'))
                    env = out.envelope()
                    self.assertEqual(
                        env['head']['snapshot'], snapshots[4 if point == 'during' else 5]
                    )
                    self.assertEqual(env['record'], env['head'])
                finally:
                    out.close()
            out, controller = reopen_after_boot(path, 'boot_recovered', 25, capacity=1)
            service = Service(Path(tmp) / 'service.db', create=True)
            receiver = Receiver(Path(tmp) / 'receiver.db', create=True)
            try:
                self.assertFalse(controller.eligible())
                self.assertEqual(controller.snapshot()['intents'], [])
                self.assertEqual(out.dropped_history_count, 2)
                fresh = out.envelope()
                out.acknowledge(service.submit(old_envelope, 25))
                self.assertEqual(out.envelope(), fresh)
                service.submit(fresh, 25)
                service.submit(old_envelope, 26)
                self.assertEqual(service.dispatch(receiver, 26), [])
            finally:
                out.close()
                service.close()
                receiver.close()


if __name__ == '__main__':
    # A separate interpreter exits without Python cleanup at the requested transaction boundary.
    point, path, transfer = sys.argv[1:]
    out = Outbox(path, capacity=1)
    if point == 'during':
        put = out._puthead

        def interrupt(head):
            put(head)
            os._exit(73)

        out._puthead = interrupt
    out.capture(json.loads(Path(transfer).read_bytes()))
    os._exit(74)
