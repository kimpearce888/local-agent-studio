"""Plan schema v1 validation rules V-01 .. V-12 (spec 5.2, 6.1)."""

import unittest

from las.schema.plan import (
    Plan, PlanFormatError, Task, Constraint, plan_variables, substitute_variables,
    validate_plan,
)
from las.tools import KNOWN_TOOLS


def base_plan() -> Plan:
    plan = make_valid()
    return plan


def make_valid() -> Plan:
    from tests.planlib import make_plan
    return make_plan()


class TestParse(unittest.TestCase):
    def test_valid_plan_parses(self):
        plan = make_valid()
        obj = plan.to_obj()
        again = Plan.from_obj(obj)
        self.assertEqual(again.to_obj(), obj)

    def test_v01_not_json_object(self):
        with self.assertRaises(PlanFormatError):
            Plan.from_obj([1, 2, 3])

    def test_v01_missing_fields(self):
        with self.assertRaises(PlanFormatError):
            Plan.from_obj({"schema_version": 1})

    def test_v01_wrong_schema_version(self):
        plan = make_valid()
        obj = plan.to_obj()
        obj["schema_version"] = 2
        with self.assertRaises(PlanFormatError):
            Plan.from_obj(obj)


class TestValidationRules(unittest.TestCase):
    def setUp(self):
        self.plan = make_valid()

    def issues(self, **kwargs):
        return validate_plan(self.plan, known_tools=KNOWN_TOOLS, **kwargs)

    def rules(self, **kwargs):
        return [i.rule for i in self.issues(**kwargs)]

    def test_valid_plan_has_no_issues(self):
        self.assertEqual(self.issues(), [])

    def test_v02_duplicate_task_id(self):
        self.plan.tasks[1].id = "t1"
        self.assertIn("V-02", self.rules())

    def test_v02_bad_task_id(self):
        self.plan.tasks[0].id = "T1"
        self.assertIn("V-02", self.rules())

    def test_v03_needs_later_task(self):
        self.plan.tasks[0].needs = ["t2"]
        self.assertIn("V-03", self.rules())

    def test_v03_needs_unknown_task(self):
        self.plan.tasks[0].needs = ["t9"]
        self.assertIn("V-03", self.rules())

    def test_v04_cycle_detected(self):
        self.plan.tasks[0].needs = ["t2"]
        self.plan.tasks[1].needs = ["t1"]
        self.assertIn("V-04", self.rules())

    def test_v05_no_instructions(self):
        self.plan.tasks[0].instructions = ""
        self.assertIn("V-05", self.rules())

    def test_v05_short_instructions(self):
        self.plan.tasks[0].instructions = "too short"
        self.assertIn("V-05", self.rules())

    def test_v05_long_title(self):
        self.plan.tasks[0].title = "x" * 81
        self.assertIn("V-05", self.rules())

    def test_v06_too_many_tasks(self):
        for i in range(4, 35):
            self.plan.tasks.append(Task(
                id=f"t{i}", title=f"Task {i}",
                instructions="Do something substantial for this task.", needs=[f"t{i-1}"]))
        issues = validate_plan(self.plan, known_tools=KNOWN_TOOLS, max_tasks=30)
        self.assertIn("V-06", [i.rule for i in issues])

    def test_v07_too_many_needs(self):
        for i in range(4, 12):
            self.plan.tasks.insert(-1, Task(
                id=f"x{i}", title=f"X {i}", instructions="Do something for this task.",
                needs=["t1"]))
        self.plan.tasks[-1].needs = [t.id for t in self.plan.tasks[:9]]
        self.assertIn("V-07", self.rules())

    def test_v08_middle_task_unused(self):
        # t2 collects from t1 only; t2 is used by t3, so break that instead.
        self.plan.tasks[2].needs = ["t1"]      # t2 now unused by anyone
        self.assertIn("V-08", self.rules())

    def test_v09_constraint_without_text(self):
        self.plan.constraints.append(Constraint(id="c9", text=""))
        self.assertIn("V-09", self.rules())

    def test_v09_duplicate_constraint_id(self):
        self.plan.constraints.append(
            Constraint(id="c1", text="Another constraint text here."))
        self.assertIn("V-09", self.rules())

    def test_v10_scope_names_missing_task(self):
        self.plan.constraints[0].scope = ["t9"]
        self.assertIn("V-10", self.rules())

    def test_v10_task_names_unknown_constraint(self):
        self.plan.tasks[0].constraints.append("c99")
        self.assertIn("V-10", self.rules())

    def test_v11_unknown_tool(self):
        self.plan.tasks[0].tools.append("browse")
        self.assertIn("V-11", self.rules())

    def test_v12_variable_without_value(self):
        self.plan.tasks[0].instructions = "Write about {{topic}} in depth."
        self.assertIn("V-12", self.rules())
        issues = validate_plan(self.plan, known_tools=KNOWN_TOOLS,
                               variables={"topic": "cats"})
        self.assertNotIn("V-12", [i.rule for i in issues])


class TestVariables(unittest.TestCase):
    def test_plan_variables(self):
        plan = make_valid()
        plan.tasks[0].title = "Research {{topic}}"
        plan.tasks[1].instructions = "Draft about {{topic}} for {{audience}}."
        self.assertEqual(plan_variables(plan), ["topic", "audience"])

    def test_substitute(self):
        plan = make_valid()
        plan.tasks[0].instructions = "Write about {{topic}}."
        substitute_variables(plan, {"topic": "gardening"})
        self.assertEqual(plan.tasks[0].instructions, "Write about gardening.")


class TestConstraintScoping(unittest.TestCase):
    def test_task_constraints_inherit_scope(self):
        plan = make_valid()
        # c1 has scope "all"; t1 names both c1 and c2.
        ids = {c.id for c in plan.task_constraints(plan.tasks[0])}
        self.assertEqual(ids, {"c1", "c2"})
        # Scope c1 to t2 only, and drop t1's explicit reference.
        plan.constraints[0].scope = ["t2"]
        plan.tasks[0].constraints = ["c2"]
        ids_t1 = {c.id for c in plan.task_constraints(plan.tasks[0])}
        self.assertEqual(ids_t1, {"c2"})
        ids_t2 = {c.id for c in plan.task_constraints(plan.tasks[1])}
        self.assertEqual(ids_t2, {"c1", "c2"})


if __name__ == "__main__":
    unittest.main()
