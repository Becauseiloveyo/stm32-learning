"""Failure-path checks for the T07 coordinator receipt, no device dependencies."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import notification_acceptance as runner


class ReceiptTests(unittest.TestCase):
    def good(self):
        group = {"passed": True, "exit_code": 0, "test_count": 1}
        return dict(suite="notification", passed=True, exit_code=0, test_count=2,
                    sources_stable_during_run=True, artifacts_stable_during_run=True,
                    synthetic="SYNTHETIC_UNCALIBRATED", collection_authorized=False,
                    groups={"protocol": dict(group), "boundaries": dict(group)},
                    source_sha256={"source": "hash"}, artifact_sha256={"accepted/receipt.json": "hash"},
                    artifact_verification={"exit_code": 0}, trace_verification={"passed": True})

    def check(self, receipt):
        return runner.validate_receipt(receipt, {"source": "hash"}, {"accepted/receipt.json": "hash"})

    def test_required_evidence_cannot_be_removed(self):
        self.assertEqual(self.check(self.good()), [])
        for key in ("groups", "source_sha256", "artifact_sha256", "artifact_verification", "trace_verification",
                    "sources_stable_during_run", "artifacts_stable_during_run", "collection_authorized"):
            candidate = self.good()
            del candidate[key]
            with self.subTest(key=key):
                self.assertTrue(self.check(candidate))

    def test_malformed_or_failed_group_never_passes(self):
        for groups in ([], None, {}, {"protocol": {}}, {"protocol": 1, "boundaries": 1}):
            candidate = self.good()
            candidate["groups"] = groups
            self.assertTrue(self.check(candidate))
        for field, value in (("test_count", True), ("test_count", 0), ("passed", False), ("exit_code", 1)):
            candidate = self.good()
            candidate["groups"]["protocol"][field] = value
            self.assertTrue(self.check(candidate))

    def test_no_empty_skipped_or_failed_run_accepted(self):
        for output, code in (("Ran 0 tests in 0.1s\n\nOK\n", 0),
                             ("Ran 1 test in 0.1s\n\nOK (skipped=1)\n", 0),
                             ("Ran 1 test in 0.1s\n\nFAILED (failures=1)\n", 1),
                             ("OK\n", 0)):
            self.assertFalse(runner.parse_unittest(subprocess.CompletedProcess([], code, "", output))["passed"])
        result = subprocess.CompletedProcess([], 0, "", "Ran 2 tests in 0.1s\n\nOK\n")
        self.assertTrue(runner.parse_unittest(result)["passed"])

    def test_changed_source_or_artifact_invalidates_receipt(self):
        self.assertTrue(runner.validate_receipt(self.good(), {"source": "changed"}, {"accepted/receipt.json": "hash"}))
        self.assertTrue(runner.validate_receipt(self.good(), {"source": "hash"}, {"accepted/receipt.json": "changed"}))
        candidate = self.good()
        candidate["test_count"] = 3
        self.assertTrue(self.check(candidate))

    def test_timeout_or_missing_source_replaces_old_success(self):
        for failure in (subprocess.TimeoutExpired(["fake"], 1), OSError("missing dependency")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "receipt.json"
                path.write_text(json.dumps(self.good()), encoding="utf-8")
                with patch.object(runner, "RECEIPT", path), \
                     patch.object(runner, "source_hashes", return_value={"source": "hash"}), \
                     patch.object(runner, "artifact_hashes", return_value={"accepted/receipt.json": "hash"}), \
                     patch.object(runner, "run_command", side_effect=failure):
                    receipt = runner.record()
                self.assertFalse(receipt["passed"])
                self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["passed"])


if __name__ == "__main__":
    unittest.main()
