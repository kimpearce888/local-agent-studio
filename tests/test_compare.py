"""OBS-6: compare two runs - plan diff and output diff."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.execution import Engine
from las.modules.reporting import compare_runs, render_compare
from las.modules.storage import Store
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class CompareBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()

    def finished_run(self, plan, summary_suffix=""):
        turns = []
        for t in plan.tasks:
            turn = success_turn(plan, t.id)
            turn["text"] = turn["text"].replace(
                "Done.", "Done." + summary_suffix)
            turns.append(turn)
        engine, run_id, driver, results = run_with_mock(plan, turns, self.data_dir)
        return engine, run_id


class TestCompare(CompareBase):
    def test_identical_plans_produce_empty_plan_diff(self):
        plan = make_plan()
        _, run_a = self.finished_run(plan)
        _, run_b = self.finished_run(plan)
        diff = compare_runs(Store(self.data_dir), run_a, run_b)
        self.assertEqual(diff["plan_diff"]["tasks_added"], [])
        self.assertEqual(diff["plan_diff"]["tasks_removed"], [])
        self.assertEqual(diff["plan_diff"]["tasks_changed"], [])

    def test_plan_and_output_differences_detected(self):
        plan_a = make_plan()
        engine = Engine(self.data_dir)
        _, run_a = self.finished_run(plan_a)
        # Run B: one task's instructions change, one handoff changes, and
        # an extra task disappears.
        plan_b = make_plan(tasks=plan_a.tasks[:len(plan_a.tasks) - 1]
                           if len(plan_a.tasks) > 2 else plan_a.tasks)
        from tests.planlib import make_plan as _mp
        plan_b = _mp()
        plan_b.tasks[1].instructions = ("Write the answer with twice the "
                                        "detail and a source list attached.")
        turns = [success_turn(plan_b, t.id) for t in plan_b.tasks]
        turns[0]["text"] = turns[0]["text"].replace("Done.", "First pass done.")
        _, run_b = self.finished_run_from(plan_b, turns)
        diff = compare_runs(Store(self.data_dir), run_a, run_b)
        changed_ids = [c["task_id"] for c in diff["plan_diff"]["tasks_changed"]]
        self.assertIn(plan_b.tasks[1].id, changed_ids)
        out = next(o for o in diff["output_diff"]
                   if o["task_id"] == plan_b.tasks[0].id)
        self.assertTrue(out["handoff_changed"])
        text = render_compare(diff)
        self.assertIn("run A:", text)
        self.assertIn("handoff changed", text)

    def finished_run_from(self, plan, turns):
        engine, run_id, driver, results = run_with_mock(plan, turns, self.data_dir)
        return engine, run_id

    def test_cli_compare(self):
        plan = make_plan()
        _, run_a = self.finished_run(plan)
        _, run_b = self.finished_run(plan)
        code, out = self.run_cli("compare", run_a, run_b)
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("identical task sets", out)
        code, out = self.run_cli("compare", run_a, "run-does-not-exist")
        self.assertEqual(code, EXIT_INVALID)


if __name__ == "__main__":
    unittest.main()
