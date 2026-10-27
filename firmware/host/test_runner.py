"""Receipt behavior tests using in-memory substitutes; never fabricate real receipts."""

import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from firmware.host import run as runner


class RunnerTests(unittest.TestCase):
    def execute(self, results=None, hashes=None, compiler_error=None):
        results = results or [dict(passed=True, check_count=2, commands=[], metrics={})] * 3
        modules = [SimpleNamespace(run_validation=lambda *a, r=r: r) for r in results]
        with patch.object(runner, "source_hashes", side_effect=hashes or [{"a": "b"}, {"a": "b"}]), \
             patch.object(runner, "resolve_compiler", return_value="test-gcc", side_effect=compiler_error), \
             patch.object(runner, "compiler_identity", return_value={"target": "test-host"}), \
             patch.object(runner.importlib, "import_module", side_effect=modules), \
             patch.object(runner.RECEIPT.__class__, "write_text") as write:
            result = runner.run(record=False)
            write.assert_not_called()
            return result

    def test_complete(self):
        r = self.execute()
        self.assertTrue(r["passed"])
        self.assertEqual(r["test_count"], 6)
        self.assertEqual(set(r["results"]), {"measurement", "energy", "integration"})

    def test_zero_checks(self):
        r = self.execute([dict(passed=True, check_count=0)])
        self.assertFalse(r["passed"])
        self.assertEqual(r["exit_code"], 1)

    def test_boolean_count(self):
        self.assertFalse(self.execute([dict(passed=True, check_count=True)])["passed"])

    def test_explicit_failure(self):
        self.assertFalse(self.execute([dict(passed=False, check_count=9)])["passed"])

    def test_source_change(self):
        r = self.execute(hashes=[{"a": "old"}, {"a": "new"}])
        self.assertFalse(r["passed"])
        self.assertFalse(r["sources_stable_during_run"])

    def test_compiler_missing(self):
        r = self.execute(compiler_error=FileNotFoundError("test compiler missing"))
        self.assertFalse(r["passed"])
        self.assertEqual(r["test_count"], 0)
        self.assertIn("test compiler missing", r["error"])

    def test_malformed_result(self):
        self.assertFalse(self.execute([None])["passed"])


def run_checks():
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RunnerTests))
    if not result.wasSuccessful():
        raise AssertionError(stream.getvalue())
    return {"check_count": result.testsRun, "output": stream.getvalue()}


if __name__ == "__main__":
    unittest.main()
