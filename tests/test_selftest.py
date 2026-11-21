"""Selftest module tests (DST-2 step 5).

Owned by the ``selftest`` module: the built-in three-task plan, its hard
caps, and the contributed ``studio selftest`` command.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.cli import EXIT_OK, EXIT_RUN_FAILED, main
from las.modules.selftest import (
    SELFTEST_TOKEN_CAP, SELFTEST_WALL_MINUTES, run_selftest, selftest_plan,
)


class SelftestTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")

    def tearDown(self):
        os.environ.pop("LAS_MOCK_SCRIPT", None)
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()


class TestSelftestPlan(SelftestTestBase):
    def test_plan_is_three_tasks_with_constraints(self):
        plan = selftest_plan()
        self.assertEqual(len(plan.tasks), 3)
        self.assertGreaterEqual(len(plan.constraints), 2)
        # The self-test itself must satisfy plan validation.
        from las.schema.plan import validate_plan
        self.assertEqual(validate_plan(plan), [])

    def test_hard_caps_are_spec_defaults(self):
        self.assertEqual(SELFTEST_TOKEN_CAP, 20_000)
        self.assertEqual(SELFTEST_WALL_MINUTES, 5)


def mock_installed() -> bool:
    """The default self-test path uses the optional providers.mock module."""
    try:
        from las.app.registry import ModuleRegistry
        ModuleRegistry().get("providers.mock")
        return True
    except Exception:  # noqa: BLE001 - ModuleNotInstalledError
        return False


class TestRunSelftest(SelftestTestBase):
    @unittest.skipUnless(mock_installed(), "providers.mock module not installed")
    def test_passes_offline_against_mock(self):
        result = run_selftest(self.data_dir)
        self.assertTrue(result.ok, result.error)
        # One result per task plus the run result; every step passed.
        self.assertEqual(len(result.steps), len(selftest_plan().tasks) + 1)
        self.assertTrue(all(step["ok"] for step in result.steps))
        self.assertGreater(result.tokens_used, 0)

    @unittest.skipUnless(mock_installed(), "providers.mock module not installed")
    def test_cli_selftest_passes(self):
        code, out = self.run_cli("selftest")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("Self-test passed", out)

    @unittest.skipUnless(mock_installed(), "providers.mock module not installed")
    def test_cli_selftest_json(self):
        code, out = self.run_cli("selftest", "--json")
        self.assertEqual(code, EXIT_OK, out)
        obj = json.loads(out)
        self.assertTrue(obj["ok"])
        self.assertTrue(obj["run_id"])


if __name__ == "__main__":
    unittest.main()
