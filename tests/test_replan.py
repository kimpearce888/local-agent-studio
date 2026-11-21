"""PLN-6: re-planning of remaining tasks with an approved diff, with
user-edited tasks pinned."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.execution import DriverConfig, Engine
from las.modules.planning import Planner
from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.modules.storage import Store
from las.shared.plan_contract import task_from_obj
from tests.planlib import make_plan
from tests.util import success_turn


class ReplanBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def paused_run(self, plan):
        """A run paused after task 1, with tasks 2+ pending."""
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter(
            [success_turn(plan, t.id) for t in plan.tasks]))
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry,
                               {"default": ("mock", "mock-model")},
                               DriverConfig(failure_policy="stop"))
        driver.run(pause_after=plan.tasks[0].id)
        return engine, run_id

    @staticmethod
    def proposal_tasks(pin_t2: bool = False) -> list:
        """A valid replacement for the remaining tasks. When the user pinned
        t2 by editing it, the proposal must consume its output (V-08)."""
        needs = ["t1", "t2"] if pin_t2 else ["t1"]
        return [
            task_from_obj({"id": "fresh-a", "title": "Fresh analysis",
                           "instructions": "Analyse the handoff from the "
                                           "earlier tasks and write analysis.md.",
                           "needs": needs, "expected_output": "analysis.md",
                           "constraints": ["c1"], "role": "worker"}),
            task_from_obj({"id": "fresh-b", "title": "Fresh collect",
                           "instructions": "Collect everything into "
                                           "result.md and verify c1.",
                           "needs": ["fresh-a"], "expected_output": "result.md",
                           "constraints": ["c1"], "role": "collector"}),
        ]


class TestApplyReplan(ReplanBase):
    def test_apply_replaces_pending_and_pins_edits(self):
        plan = make_plan()
        engine, run_id = self.paused_run(plan)
        # The user edited the second task while paused: it must stay pinned.
        engine.edit_task(run_id, plan.tasks[1].id,
                         {"title": "Hand-tuned title"})
        outcome = engine.apply_replan(run_id, self.proposal_tasks(pin_t2=True),
                                      reason="test")
        state = engine.state(run_id)
        ids = [t.id for t in state.plan.tasks]
        # t1 kept (completed), t2 kept (pinned by the edit), t3 removed,
        # fresh tasks appended.
        self.assertIn("t1", ids)
        self.assertIn("t2", ids)
        self.assertNotIn("t3", ids)
        self.assertIn("fresh-a", ids)
        self.assertIn("fresh-b", ids)
        self.assertEqual(outcome["pinned"], [plan.tasks[1].id])
        self.assertEqual(outcome["removed"], [plan.tasks[2].id])

    def test_apply_rejects_invalid_proposals(self):
        plan = make_plan()
        engine, run_id = self.paused_run(plan)
        # Cycle: fresh-a needs fresh-b which comes later.
        bad = [task_from_obj({"id": "fresh-a", "title": "Bad",
                              "instructions": "This one creates a cycle "
                                              "with its own dependency.",
                              "needs": ["fresh-b"],
                              "expected_output": "x.md",
                              "constraints": ["c1"], "role": "worker"}),
               task_from_obj({"id": "fresh-b", "title": "Bad collect",
                              "instructions": "Collect into result.md "
                                              "and verify the constraint.",
                              "needs": ["t1"], "expected_output": "result.md",
                              "constraints": ["c1"], "role": "collector"})]
        with self.assertRaises(ValueError) as ctx:
            engine.apply_replan(run_id, bad)
        self.assertIn("invalid", str(ctx.exception))

    def test_replanned_run_completes_with_new_tasks(self):
        plan = make_plan()
        engine, run_id = self.paused_run(plan)
        engine.apply_replan(run_id, self.proposal_tasks())
        from las.modules.storage import Store as _Store
        _Store(self.data_dir).append(run_id, "run.resumed",
                                     {"reason": "resume after replan"})
        from tests.util import constraint_objs, handoff_text
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter([
            {"text": handoff_text("fresh-a", "Fresh analysis done.",
                                  constraint_objs(plan))},
            {"text": handoff_text("fresh-b", "Fresh collect done.",
                                  constraint_objs(plan))},
        ]))
        driver = engine.driver(run_id, registry,
                               {"default": ("mock", "mock-model")},
                               DriverConfig(failure_policy="stop"))
        driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")
        self.assertEqual(engine.state(run_id).tasks["fresh-b"].state,
                         "succeeded")


class TestPlannerReplan(ReplanBase):
    def test_planner_replan_round_trip(self):
        plan = make_plan()
        engine, run_id = self.paused_run(plan)
        reply = json.dumps({
            "tasks": [t.to_obj() for t in self.proposal_tasks()]})   # t1 kept
        planner_adapter = MockAdapter([{"text": reply}])
        planner = Planner(planner_adapter, "mock-model")
        state = engine.state(run_id)
        kept, _ = engine.replan_target(run_id)
        remaining = [t for t in state.plan.tasks if t.id not in set(kept)]
        result = planner.replan(state.plan.goal, state.plan, kept, remaining)
        self.assertTrue(result.valid, result.issues)
        self.assertEqual([t.id for t in result.plan.tasks],
                         ["fresh-a", "fresh-b"])


class TestCliReplan(ReplanBase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()

    def test_propose_then_apply_two_step(self):
        plan = make_plan()
        engine, run_id = self.paused_run(plan)
        Store(self.data_dir).save_plan({
            "schema_version": 1, "id": "replan-x", "title": "T",
            "goal": plan.goal, "created_at": plan.created_at,
            "options": plan.options,
            "constraints": [c.to_obj() for c in plan.constraints],
            "tasks": [t.to_obj() for t in self.proposal_tasks()]})
        code, out = self.run_cli("replan", run_id, "--apply", "replan-x")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("fresh-a", out)
        # Unknown proposal refused.
        code, out = self.run_cli("replan", run_id, "--apply", "nope")
        self.assertEqual(code, EXIT_INVALID)


if __name__ == "__main__":
    unittest.main()
