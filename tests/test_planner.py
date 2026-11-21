"""Planner: repair loop (AC-02), constraint extraction (AC-03), critique."""

import unittest

from las.modules.planning import (
    Planner, extract_constraint_candidates, find_source_span,
)
from las.modules.providers.mock import MockAdapter
from las.modules.planquality import validate_plan
from las.modules.security import KNOWN_TOOLS
from tests.planlib import make_plan

GOAL = ("Write a market brief about e-bikes. You must cite every figure. "
        "Never invent numbers. The report requires approval before publishing.")


def plan_reply(tasks=None, constraints=None, title="E-bike brief") -> dict:
    return {
        "title": title,
        "constraints": constraints if constraints is not None else [
            {"id": "c1", "text": "You must cite every figure.", "authority": "user"},
            {"id": "c2", "text": "Never invent numbers.", "authority": "user"},
        ],
        "tasks": tasks if tasks is not None else [
            {"id": "t1", "type": "task", "title": "Gather facts",
             "instructions": "Gather five sourced facts about the e-bike market.",
             "needs": []},
            {"id": "t2", "type": "task", "title": "Draft brief",
             "instructions": "Draft the market brief from the gathered facts.",
             "needs": ["t1"]},
            {"id": "t3", "type": "collector", "title": "Final brief",
             "instructions": "Combine everything into the final market brief.",
             "needs": ["t1", "t2"]},
        ],
    }


class TestPlannerLoop(unittest.TestCase):
    def plan(self, script, options=None):
        adapter = MockAdapter(script)
        planner = Planner(adapter, "mock-model")
        return planner.plan(GOAL, options or {"granularity": "normal", "max_tasks": 30})

    def test_valid_first_reply(self):
        result = self.plan([{"json": plan_reply()}])
        self.assertTrue(result.valid)
        self.assertEqual(result.repair_rounds, 0)
        self.assertEqual(len(result.plan.tasks), 3)
        self.assertEqual(validate_plan(result.plan, known_tools=KNOWN_TOOLS), [])

    def test_ac02_repair_after_invalid_dependency(self):
        # First reply: t5 depends on a task that comes later (V-03).
        bad = plan_reply()
        bad["tasks"][1]["needs"] = ["t3"]
        result = self.plan([{"json": bad}, {"json": plan_reply()}])
        self.assertTrue(result.valid)
        self.assertEqual(result.repair_rounds, 1)
        self.assertEqual(result.plan.task("t2").needs, ["t1"])

    def test_ac02_three_failed_repairs_give_partial_plan(self):
        bad = plan_reply()
        bad["tasks"][1]["needs"] = ["t3"]
        result = self.plan([{"json": bad}] * 4)
        self.assertFalse(result.valid)
        self.assertTrue(result.partial)
        self.assertEqual(result.repair_rounds, 3)
        # Longest valid prefix: t1 kept, truncated at the first failing task.
        self.assertEqual([t.id for t in result.plan.tasks], ["t1"])
        self.assertTrue(any("truncated at task t2" in i.message for i in result.issues))

    def test_provider_error_reported(self):
        result = self.plan([{"error": "UNREACHABLE"}])
        self.assertFalse(result.valid)
        self.assertEqual(result.error, "UNREACHABLE")

    def test_lenient_json_parsing(self):
        reply = "Here is the plan:\n```json\n" + _dumps(plan_reply()) + "\n```"
        result = self.plan([{"text": reply}])
        self.assertTrue(result.valid)


class TestConstraintExtraction(unittest.TestCase):
    def test_ac03_constraint_copied_verbatim_with_span(self):
        result = self.plan_with_goal_constraint()
        c = result.plan.constraint("c2")
        self.assertEqual(c.text, "Never invent numbers.")
        self.assertEqual(c.origin, "goal")
        self.assertIsNotNone(c.source_span)
        # The span points into the goal text.
        start, end = c.source_span
        self.assertEqual(" ".join(GOAL.split())[start:end], "Never invent numbers.")

    def plan_with_goal_constraint(self):
        adapter = MockAdapter([{"json": plan_reply()}])
        planner = Planner(adapter, "mock-model")
        return planner.plan(GOAL, {"granularity": "normal", "max_tasks": 30})

    def test_candidates_found_in_goal(self):
        candidates = extract_constraint_candidates(GOAL)
        self.assertTrue(any("must cite every figure" in c.lower() for c in candidates))
        self.assertTrue(any("never invent numbers" in c.lower() for c in candidates))

    def test_source_span_requires_verbatim_text(self):
        self.assertIsNone(find_source_span(GOAL, "Totally not in the goal."))

    def test_constraint_from_planner_origin_when_not_in_goal(self):
        reply = plan_reply(constraints=[
            {"id": "c1", "text": "A requirement the planner invented itself.", "authority": "user"}])
        adapter = MockAdapter([{"json": reply}])
        result = Planner(adapter, "m").plan(GOAL, {"max_tasks": 30})
        self.assertTrue(result.valid)
        self.assertEqual(result.plan.constraints[0].origin, "planner")
        self.assertIsNone(result.plan.constraints[0].source_span)


class TestCritique(unittest.TestCase):
    def test_critique_returns_suggestions(self):
        plan = make_plan()
        adapter = MockAdapter([
            {"json": [{"issue": "missing collector", "task_id": "t3", "note": "no collector"}]}])
        critique = Planner(adapter, "m").critique(GOAL, plan)
        self.assertEqual(len(critique), 1)
        self.assertEqual(critique[0]["issue"], "missing collector")


def _dumps(obj) -> str:
    import json
    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main()


class TestGatePreservation(unittest.TestCase):
    """Regression: the planner must not silently drop an approval gate the
    model planned (gate loss is trust-boundary loss; L-01 repair rounds
    set exactly this field)."""

    def test_draft_gate_survives_into_the_plan(self):
        reply = plan_reply()
        reply["tasks"][2]["gate_before"] = True
        result = Planner(MockAdapter([{"json": reply}]), "mock-model").plan(GOAL)
        self.assertTrue(result.valid)
        self.assertTrue(result.plan.tasks[2].gate_before)
        self.assertFalse(result.plan.tasks[0].gate_before)

    def test_schema_advertises_gate_before(self):
        from las.modules.planning import PLAN_JSON_SCHEMA, SCHEMA_AS_TEXT
        task_props = PLAN_JSON_SCHEMA["properties"]["tasks"]["items"]["properties"]
        self.assertIn("gate_before", task_props)
        self.assertIn("gate_before", SCHEMA_AS_TEXT)

    def test_gate_survives_the_repair_path(self):
        # Draft 1 breaks V-03 (needs a later task); draft 2 is valid and
        # adds an approval gate - the gate must survive the repair loop.
        first = plan_reply()
        first["tasks"][1]["needs"] = ["t3"]
        fixed = plan_reply()
        fixed["tasks"][2]["gate_before"] = True
        result = Planner(MockAdapter([{"json": first}, {"json": fixed}]),
                         "mock-model").plan(GOAL)
        self.assertTrue(result.valid)
        self.assertEqual(result.repair_rounds, 1)
        self.assertTrue(result.plan.tasks[2].gate_before)

    def test_gate_clears_l01_at_lint_time(self):
        # L-01 is a run-gate rule (lint), not a planner repair rule: the
        # ungated risky plan is flagged, the gated plan lints clean.
        from las.modules.planquality import lint
        risky = {"id": "t1", "type": "task", "title": "Publish",
                 "instructions": "Publish the brief to the site.",
                 "needs": [], "tools": ["run_command", "web_fetch"]}
        ungated = Planner(MockAdapter(
            [{"json": plan_reply(tasks=[risky])}]), "mock-model").plan(GOAL).plan
        self.assertIn("L-01", [f.rule for f in lint(ungated)])
        gated = Planner(MockAdapter(
            [{"json": plan_reply(tasks=[dict(risky, gate_before=True)])}]),
            "mock-model").plan(GOAL).plan
        self.assertEqual([f.rule for f in lint(gated)
                          if f.rule == "L-01"], [])
