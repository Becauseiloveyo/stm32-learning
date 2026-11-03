import json
import unittest
from research.integration_t11.p2.power import Battery, ETA
from research.energy_t05.contracts import Energy, measurement_components
from theory.protocol import MeasurementProtocol


class Tests(unittest.TestCase):

    def test_measurement_matches_t05_components(self):
        b = Battery()
        start = b.stored_j
        self.assertTrue(b.start_measurement())
        b.advance(140)
        work = sum(measurement_components(Energy(), MeasurementProtocol()).values()) * 3600
        self.assertAlmostEqual(b.ledger()['measurement_j'], work / ETA)
        self.assertAlmostEqual(start - b.stored_j, (work + 0.0002 * 140) / ETA)
        self.assertIsNone(b.active)

    def test_admission_and_no_future_borrowing(self):
        full = Battery()
        reserve = full.reserve_j
        b = Battery(initial_j=reserve + 1)
        b.charge(1, 0.75)
        self.assertFalse(b.start_radio())
        b.advance(20)
        self.assertTrue(b.start_radio())
        self.assertFalse(b.spend(100))
        self.assertFalse(b.start_measurement())

    def test_radio_success_and_timeout(self):
        a = Battery()
        self.assertTrue(a.start_radio())
        a.advance(5)
        self.assertFalse(a.radio_success())
        a.advance(6)
        self.assertTrue(a.radio_success())
        self.assertAlmostEqual(a.ledger()['radio_j'], 0.6 * 6 / ETA)
        b = Battery()
        b.start_radio()
        b.advance(20)
        self.assertIsNone(b.active)
        self.assertAlmostEqual(b.ledger()['radio_j'], 12 / ETA)

    def test_cancel_open_and_boundaries(self):
        b = Battery()
        b.start_measurement()
        b.advance(30)
        self.assertTrue(b.vent_open)
        self.assertTrue(b.cancel_measurement())
        self.assertFalse(b.vent_open)
        self.assertAlmostEqual(b.ledger()['measurement_j'], (0.002 * 30 + 0.1) / ETA)
        b = Battery()
        b.start_measurement()
        b.advance(50)
        self.assertFalse(b.vent_open)
        self.assertTrue(b.cancel_measurement())
        self.assertAlmostEqual(b.ledger()['measurement_j'], (0.002 * 50 + 0.2 * 20 + 0.1) / ETA)

    def test_charge_efficiency_curtail_and_empty(self):
        b = Battery()
        b.charge(1, 0.75)
        b.advance(10)
        l = b.ledger()
        self.assertAlmostEqual(l['charge_input_j'], 10)
        self.assertAlmostEqual(l['charge_battery_j'], 7.5)
        self.assertAlmostEqual(l['curtailed_j'], 7.5 - l['baseline_j'])
        self.assertAlmostEqual(b.stored_j, b.capacity_j)
        b.charge(0, 0)
        b.advance(11)
        self.assertLess(b.stored_j, b.capacity_j)
        empty = Battery(initial_j=0)
        empty.advance(10)
        self.assertGreater(empty.ledger()['unserved_baseline_j'], 0)
        self.assertFalse(empty.start_measurement())

    def test_full_charge_boundary_cost_is_not_paid_by_curtailed_energy(self):
        b = Battery()
        b.charge(1, 1)
        self.assertTrue(b.start_measurement())
        b.advance(30)
        self.assertAlmostEqual(b.stored_j, b.capacity_j - 0.05 / ETA)
        self.assertTrue(b.cancel_measurement())
        self.assertAlmostEqual(b.stored_j, b.capacity_j - 0.1 / ETA)
        l = b.ledger()
        self.assertAlmostEqual(
            l['initial_j']
            + l['charge_battery_j']
            - l['curtailed_j']
            - l['baseline_j']
            - l['measurement_j'],
            l['stored_j'],
        )

    def test_extreme_inputs_rejected(self):
        for value in (float('inf'), 10**400, 1000001):
            with self.assertRaises(ValueError):
                Battery(capacity_ah=value)
            with self.assertRaises(ValueError):
                Battery().charge(value, 1)
        with self.assertRaises(ValueError):
            Battery().spend(10**400)
        with self.assertRaises(ValueError):
            Battery().advance(10000001)

    def test_validation_and_ledger(self):
        b = Battery()
        json.dumps(b.ledger(), allow_nan=False)
        for value in (True, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                b.spend(value)
        with self.assertRaises(ValueError):
            b.advance(1.5)
        with self.assertRaises(ValueError):
            b.charge(1, 1.1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
