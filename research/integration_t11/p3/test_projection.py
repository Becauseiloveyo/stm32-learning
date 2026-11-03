import json
import unittest
from research.integration_t11.p2.replay import CASES, run_case
from research.integration_t11.p3.projection import build


class ProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build()
        cls.cases = {c["id"]: c["frames"] for c in cls.data["cases"]}

    def test_json_and_p2_actions(self):
        json.dumps(self.data, allow_nan=False)
        for name in CASES:
            expected = [(x["t"], x["action"]) for x in run_case(name)["trace"]]
            actual = [(f["t"], a) for f in self.cases[name] for a in f["actions"]]
            self.assertEqual(expected, actual, name)

    def test_offline_remote_and_reconnect(self):
        frames = self.cases["wet_reconnect"]
        offline = next(f for f in frames if f["t"] == 62)
        self.assertEqual(offline["local"]["wet"], "wet")
        self.assertNotEqual(offline["remote"]["wet"], "wet")
        self.assertGreater(offline["local_seq"], offline["remote_seq"])
        received = next(
            f for f in frames if f["t"] > 88 and f["remote"]["wet"] == "wet"
        )
        self.assertEqual(received["remote_seq"], received["saved_seq"])

    def test_expiry_and_boundaries(self):
        frames = {f["t"]: f for f in self.cases["forecast_boundary"]}
        self.assertEqual(frames[30]["local"]["forecast"], "upcoming_risk")
        self.assertEqual(frames[60]["local"]["forecast"], "risk")
        self.assertEqual(frames[90]["local"]["forecast"], "unavailable")
        frames = {f["t"]: f for f in self.cases["normal_cycle"]}
        self.assertIsNotNone(frames[26]["local"]["remaining_s"])
        self.assertIsNone(frames[100]["local"]["remaining_s"])

    def test_remote_forecast_and_contradiction(self):
        frames = {f["t"]: f for f in self.cases["forecast_boundary"]}
        for t, expected in ((36, "upcoming_risk"), (60, "risk"), (90, "unavailable")):
            self.assertEqual(frames[t]["remote"]["forecast"], expected)
            self.assertEqual(frames[t]["received_s"], 36)
        self.assertFalse(frames[60]["link"])
        frames = {f["t"]: f for f in self.cases["forecast_wet"]}
        self.assertEqual(frames[62]["local"]["forecast"], "no_risk")
        self.assertEqual(frames[62]["local"]["wet"], "wet")
        self.assertEqual(frames[68]["remote"]["forecast"], "no_risk")
        self.assertEqual(frames[68]["remote"]["wet"], "wet")

    def test_mismatch_and_fault_next_frame(self):
        frames = {f["t"]: f for f in self.cases["model_mismatch"]}
        self.assertEqual(
            frames[26]["local"]["prediction"], "coverage_or_model_mismatch"
        )
        self.assertIsNone(frames[26]["local"]["remaining_s"])
        frames = {f["t"]: f for f in self.cases["fault_revoke"]}
        self.assertEqual(frames[26]["local"]["prediction"], "conditional")
        for t in (35, 36):
            self.assertEqual(frames[t]["local"]["prediction"], "unavailable")
            self.assertIsNone(frames[t]["local"]["remaining_s"])
        self.assertEqual(frames[41]["remote"]["prediction"], "unavailable")

    def test_receive_clock_and_dedup(self):
        frames = self.cases["ack_lost"]
        previous = None
        for frame in frames:
            if "simulated_receiver" in frame["actions"]:
                self.assertEqual(frame["received_s"], frame["t"])
                if previous and frame["remote_seq"] == previous["remote_seq"]:
                    self.assertEqual(frame["newly_notified"], 0)
            elif "receiver_query" in frame["actions"] and previous:
                self.assertEqual(frame["received_s"], previous["received_s"])
            previous = frame

    def test_queue_and_revocation(self):
        self.assertGreater(max(f["drops"] for f in self.cases["queue_full"]), 0)
        f = next(f for f in self.cases["contact_missing"] if f["t"] == 35)
        self.assertEqual(f["local"]["contact"], "missing")
        self.assertIsNone(f["local"]["remaining_s"])


if __name__ == "__main__":
    unittest.main()
