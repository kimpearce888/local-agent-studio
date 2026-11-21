"""Handoff validation rules H-01 .. H-08 (spec 5.5, 6.2).

H-03 is the constraint-preservation invariant: constraint text is compared
by SHA-256 of its whitespace-normalized form.  A dropped or reworded
constraint must fail validation (RUN-3, AC-05).
"""

import hashlib
import json
import unittest

from las.schema.handoff import (
    EvidenceContext, Handoff, constraint_hash, extract_handoff, normalize_ws,
    validate_handoff,
)
from las.schema.plan import Constraint
from tests.planlib import constraint_objs, handoff_text, make_plan


class TestExtraction(unittest.TestCase):
    def test_extract_valid_block(self):
        text = handoff_text("t1", "Did things.", [])
        handoff = extract_handoff(text)
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.task_id, "t1")

    def test_extract_with_code_fence(self):
        obj = {"task_id": "t1", "status": "done", "summary": "ok",
               "key_facts": [], "binding_constraints": []}
        text = "<handoff>\n```json\n" + json.dumps(obj) + "\n```\n</handoff>"
        self.assertEqual(extract_handoff(text).task_id, "t1")

    def test_extract_missing_block(self):
        self.assertIsNone(extract_handoff("just a plain reply"))

    def test_extract_bad_json(self):
        self.assertIsNone(extract_handoff("<handoff>{not json</handoff>"))


class TestConstraintHash(unittest.TestCase):
    def test_normalize_ws(self):
        self.assertEqual(normalize_ws("never  invent\n numbers "), "never invent numbers")

    def test_hash_changes_with_text(self):
        self.assertNotEqual(constraint_hash("Never invent numbers."),
                            constraint_hash("Never invent figures."))

    def test_hash_stable_across_whitespace(self):
        self.assertEqual(constraint_hash("Never invent numbers."),
                         constraint_hash("Never   invent\nnumbers."))


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.task = self.plan.tasks[0]

    def validate(self, handoff):
        return validate_handoff(handoff, self.task, self.plan)

    def rules(self, handoff):
        return [i.rule for i in self.validate(handoff)]

    def valid_handoff_text(self, summary="Gathered the facts.", **kwargs):
        return handoff_text("t1", summary, constraint_objs(self.plan), **kwargs)

    def test_valid_handoff(self):
        self.assertEqual(self.rules(extract_handoff(self.valid_handoff_text())), [])

    def test_h01_missing_block(self):
        self.assertIn("H-01", self.rules(None))

    def test_h01_wrong_task(self):
        text = handoff_text("t2", "oops", constraint_objs(self.plan))
        self.assertIn("H-01", self.rules(extract_handoff(text)))

    def test_h02_bad_status(self):
        text = self.valid_handoff_text(status="finished")
        self.assertIn("H-02", self.rules(extract_handoff(text)))

    def test_h03_dropped_constraint(self):
        text = handoff_text("t1", "Gathered.", constraint_objs(self.plan, drop=["c2"]))
        self.assertIn("H-03", self.rules(extract_handoff(text)))

    def test_h03_reworded_constraint(self):
        text = handoff_text("t1", "Gathered.", constraint_objs(
            self.plan, reword={"c2": "Never fabricate numbers."}))
        rules = self.rules(extract_handoff(text))
        self.assertIn("H-03", rules)

    def test_h03_whitespace_only_change_passes(self):
        objs = constraint_objs(self.plan)
        objs[1]["text"] = "Never   invent\nnumbers."     # same normalized text
        text = handoff_text("t1", "Gathered.", objs)
        self.assertEqual(self.rules(extract_handoff(text)), [])

    def test_h04_duplicate_ids(self):
        objs = constraint_objs(self.plan)
        objs.append(dict(objs[0]))
        text = handoff_text("t1", "Gathered.", objs)
        self.assertIn("H-04", self.rules(extract_handoff(text)))

    def test_h04_new_constraint_reuses_id(self):
        objs = constraint_objs(self.plan)
        objs.append({"id": "c1", "text": "A completely different requirement.", "state": "active"})
        text = handoff_text("t1", "Gathered.", objs)
        self.assertIn("H-04", self.rules(extract_handoff(text)))

    def test_h05_resolved_without_evidence(self):
        objs = constraint_objs(self.plan, state="resolved")
        text = handoff_text("t1", "Done.", objs)
        self.assertIn("H-05", self.rules(extract_handoff(text)))

    def test_h05_model_self_claim_rejected(self):
        objs = constraint_objs(self.plan, resolve_evidence={
            "c1": {"source": "model", "ref": "I checked it myself"}})
        text = handoff_text("t1", "Done.", objs)
        self.assertIn("H-05", self.rules(extract_handoff(text)))

    def test_h05_tool_evidence_accepted(self):
        objs = constraint_objs(self.plan, resolve_evidence={
            "c1": {"source": "tool:read_file", "ref": "facts.md"}})
        # The plan constraint's authority must match the evidence source.
        self.plan.constraints[0].authority = "tool:read_file"
        text = handoff_text("t1", "Done.", objs)
        evidence = EvidenceContext(attempt_tool_calls={"read_file"})
        issues = validate_handoff(extract_handoff(text), self.task, self.plan,
                                  evidence=evidence)
        self.assertEqual([i.rule for i in issues], [])

    def test_h05_user_evidence_needs_approval_record(self):
        objs = constraint_objs(self.plan, resolve_evidence={
            "c1": {"source": "user", "ref": "gate-1"}})
        text = handoff_text("t1", "Done.", objs)
        no_user = validate_handoff(extract_handoff(text), self.task, self.plan,
                                   evidence=EvidenceContext())
        self.assertIn("H-05", [i.rule for i in no_user])
        with_user = validate_handoff(
            extract_handoff(text), self.task, self.plan,
            evidence=EvidenceContext(user_approvals={"gate-1"}))
        self.assertEqual([i.rule for i in with_user], [])

    def test_h06_fact_without_source(self):
        text = self.valid_handoff_text(
            key_facts=[{"fact": "Something true", "source": ""}])
        self.assertIn("H-06", self.rules(extract_handoff(text)))

    def test_h06_blocking_issue_needs_constraint(self):
        text = self.valid_handoff_text(extra={
            "open_issues": [{"issue": "stuck", "constraint_id": "", "blocking": True}]})
        self.assertIn("H-06", self.rules(extract_handoff(text)))

    def test_h08_long_summary(self):
        text = self.valid_handoff_text(summary="word " * 401)
        self.assertIn("H-08", self.rules(extract_handoff(text)))


class TestDeliverables(unittest.TestCase):
    def setUp(self):
        import tempfile
        from las.workspace import Workspace
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(self.tmp.name)
        self.workspace.write_file("out.md", "# Result\n")
        self.plan = make_plan()
        self.task = self.plan.tasks[0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_h07_missing_file(self):
        objs = constraint_objs(self.plan)
        text = handoff_text("t1", "Done.", objs,
                            deliverables=[{"path": "nope.md"}])
        issues = validate_handoff(extract_handoff(text), self.task, self.plan,
                                  workspace=self.workspace)
        self.assertIn("H-07", [i.rule for i in issues])

    def test_h07_hash_mismatch(self):
        objs = constraint_objs(self.plan)
        text = handoff_text("t1", "Done.", objs,
                            deliverables=[{"path": "out.md", "sha256": "0" * 64}])
        issues = validate_handoff(extract_handoff(text), self.task, self.plan,
                                  workspace=self.workspace)
        self.assertIn("H-07", [i.rule for i in issues])

    def test_h07_hash_match(self):
        import hashlib
        digest = hashlib.sha256(b"# Result\n").hexdigest()
        objs = constraint_objs(self.plan)
        text = handoff_text("t1", "Done.", objs,
                            deliverables=[{"path": "out.md", "sha256": digest}])
        issues = validate_handoff(extract_handoff(text), self.task, self.plan,
                                  workspace=self.workspace)
        self.assertEqual([i.rule for i in issues], [])


if __name__ == "__main__":
    unittest.main()
