"""Lint rules L-01 .. L-09 (spec 5.3, BLD-7)."""

import unittest

from las.linter import lint, errors as lint_errors
from las.schema.plan import Task
from tests.planlib import make_plan


class TestLint(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()

    def rules(self, **kwargs):
        return [f.rule for f in lint(self.plan, **kwargs)]

    def test_clean_plan_lints_clean(self):
        findings = lint(self.plan)
        errors = lint_errors(findings)
        self.assertEqual(errors, [])

    def test_l01_three_flags_need_gate(self):
        # t1 uses web_fetch (A, C); t2 needs t1 (inherits A) and runs commands
        # (B, C) -> three of three with no gate -> L-01 error (AC-08).
        self.plan.tasks[0].tools = ["read_file", "web_fetch"]
        self.plan.tasks[1].tools = ["read_file", "run_command"]
        findings = lint(self.plan)
        l01 = [f for f in findings if f.rule == "L-01"]
        self.assertEqual(len(l01), 1)
        self.assertEqual(l01[0].task_id, "t2")
        self.assertEqual(l01[0].severity, "error")
        self.assertIn("Add an approval gate", l01[0].message)

    def test_l01_cleared_by_gate(self):
        self.plan.tasks[0].tools = ["read_file", "web_fetch"]
        self.plan.tasks[1].tools = ["read_file", "run_command"]
        self.plan.tasks[1].gate_before = True
        self.assertNotIn("L-01", self.rules())

    def test_l04_unused_result(self):
        self.plan.tasks[2].needs = ["t1"]     # t2's result now unused
        self.assertIn("L-04", self.rules())

    def test_l05_constraint_not_carried(self):
        # Scope to nothing and referenced by no task.
        self.plan.constraints[0].scope = []
        for t in self.plan.tasks:
            t.constraints = [c for c in t.constraints if c != "c1"]
        self.assertIn("L-05", self.rules())

    def test_l06_long_instructions(self):
        self.plan.tasks[0].instructions = "Do this. " * 400
        self.assertIn("L-06", self.rules())

    def test_l07_tools_without_capability(self):
        findings = lint(self.plan, tool_calling=False)
        self.assertIn("L-07", [f.rule for f in findings])

    def test_l08_collector_with_one_input(self):
        self.plan.tasks[2].needs = ["t1"]
        self.assertIn("L-08", self.rules())

    def test_l09_provider_unreachable(self):
        self.plan.tasks[0].model = {"provider_id": "gone", "model_id": "m"}
        findings = lint(self.plan, provider_reachable={"gone": False},
                        default_provider_id="mock")
        l09 = [f for f in findings if f.rule == "L-09"]
        self.assertEqual(len(l09), 1)
        self.assertEqual(l09[0].severity, "error")
        self.assertEqual(l09[0].task_id, "t1")

    def test_l03_warning_on_large_context_use(self):
        # Force a small context so the predicted input exceeds 80%.
        findings = lint(self.plan, context_length=1000)
        self.assertTrue(any(f.rule in ("L-02", "L-03") for f in findings))


if __name__ == "__main__":
    unittest.main()
