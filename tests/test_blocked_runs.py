"""The honest-blocker path (spec 5.5, four-field blocker).

A valid handoff whose status is ``blocked`` - or that carries a blocking
open issue - stops the run in the ``blocked`` terminal state instead of
failing or silently succeeding, and resuming re-attempts the blocked
task.  A blocked handoff without a blocking open issue is ownership
deferral and fails H-02.
"""

import tempfile
import unittest

from tests.planlib import constraint_objs, handoff_text, make_plan
from tests.util import run_with_mock, success_turn


def blocked_turn(plan, task_id, issue="The source file is missing.",
                 constraint_id="c1", with_blocker=True):
    """A final message with a blocked handoff (optionally malformed)."""
    open_issues = ([{"issue": issue, "constraint_id": constraint_id,
                     "blocking": True}] if with_blocker else [])
    return {"text": handoff_text(
        task_id, "Stopped: prerequisite missing.", constraint_objs(plan),
        status="blocked",
        extra={"open_issues": open_issues})}


class BlockerValidationTests(unittest.TestCase):
    def test_blocked_handoff_requires_a_blocking_issue(self):
        from las.modules.handoffs import Handoff, validate_handoff
        plan = make_plan()
        task = plan.tasks[0]
        obj = {
            "task_id": task.id, "status": "blocked",
            "summary": "Cannot proceed.",
            "key_facts": [], "decisions": [], "deliverables": [],
            "binding_constraints": constraint_objs(plan),
            "open_issues": [],
        }
        issues = validate_handoff(Handoff.from_obj(obj), task, plan)
        self.assertTrue(any(i.rule == "H-02" for i in issues),
                        [str(i) for i in issues])

    def test_blocked_handoff_with_blocker_passes(self):
        from las.modules.handoffs import Handoff, validate_handoff
        plan = make_plan()
        task = plan.tasks[0]
        obj = {
            "task_id": task.id, "status": "blocked",
            "summary": "Cannot proceed without the source file.",
            "key_facts": [], "decisions": [], "deliverables": [],
            "binding_constraints": constraint_objs(plan),
            "open_issues": [{"issue": "The source file is missing.",
                             "constraint_id": "c1", "blocking": True}],
        }
        issues = validate_handoff(Handoff.from_obj(obj), task, plan)
        self.assertEqual(issues, [])


class DecorationRecoveryTests(unittest.TestCase):
    """H-03 must not fail a model for copying the engine's own rendered
    constraint block verbatim (found by the reference-model benchmark:
    glm-4-plus copied the whole decorated block into binding_constraints
    text and every run died HANDOFF-INVALID)."""

    def test_rendered_block_copy_passes_h03(self):
        from las.modules.handoffs import Handoff, validate_handoff
        from las.shared.plan_contract import Constraint
        from tests.planlib import make_plan as _mp
        # Constraints with the full four-field decoration, exactly as
        # the reference-model benchmark plans carried them.
        plan = _mp(constraints=[
            Constraint(id="c1", text="You must cite every figure.",
                       prerequisite="Figures come from workspace files",
                       authority="user",
                       fallback="Cite the file path or omit the figure"),
            Constraint(id="c2", text="Never invent numbers.",
                       prerequisite="Data must come from existing records",
                       authority="user",
                       fallback="Use only available data"),
        ])
        task = plan.tasks[0]
        c = plan.constraints[0]
        # Exactly what the reference model produced (2026-11-22 run):
        # the text plus the renderer's prerequisite/authority/fallback
        # sub-lines, and the [id] header variant.
        decorated = (f"{c.text}\n"
                     f"  prerequisite: {c.prerequisite}\n"
                     f"  authority: {c.authority}\n"
                     f"  fallback while unresolved: {c.fallback}")
        with_header = f"[{c.id}] (state: active) {decorated}"
        with_text_line = (f'[{c.id}] (state: active)\n  text: "{c.text}"\n'
                          f"  prerequisite: {c.prerequisite}\n"
                          f"  authority: {c.authority}")
        for copied in (c.text, decorated, with_header, with_text_line):
            obj = {
                "task_id": task.id, "status": "blocked",
                "summary": "Stopped honestly.",
                "key_facts": [], "decisions": [], "deliverables": [],
                "binding_constraints": [
                    {"id": c.id, "text": copied, "state": "active"},
                    {"id": plan.constraints[1].id,
                     "text": plan.constraints[1].text, "state": "active"},
                ],
                "open_issues": [{"issue": "missing input",
                                 "constraint_id": c.id, "blocking": True}],
            }
            issues = validate_handoff(Handoff.from_obj(obj), task, plan)
            self.assertEqual(
                [i.rule for i in issues], [],
                f"decorated copy must pass H-03: {copied[:40]!r} -> {issues}")

    def test_real_rewording_still_fails_h03(self):
        from las.modules.handoffs import Handoff, validate_handoff
        plan = make_plan()
        task = plan.tasks[0]
        obj = {
            "task_id": task.id, "status": "done", "summary": "Done.",
            "key_facts": [], "decisions": [], "deliverables": [],
            "binding_constraints": [
                {"id": plan.constraints[0].id,
                 "text": "A paraphrase the engine never rendered.",
                 "state": "active"},
                {"id": plan.constraints[1].id,
                 "text": plan.constraints[1].text, "state": "active"},
            ],
            "open_issues": [],
        }
        issues = validate_handoff(Handoff.from_obj(obj), task, plan)
        self.assertTrue(any(i.rule == "H-03" for i in issues))


class BlockedRunTests(unittest.TestCase):
    def test_blocking_issue_blocks_the_run(self):
        plan = make_plan()
        with tempfile.TemporaryDirectory() as data_dir:
            engine, run_id, _driver, results = run_with_mock(
                plan, [blocked_turn(plan, "t1")], data_dir)
            state = engine.state(run_id)
            self.assertEqual(state.status, "blocked")
            self.assertEqual(state.task_state("t1").state, "blocked")
            self.assertIn("run_blocked", [r.kind for r in results])
            events = [e.type for e in state.events]
            self.assertIn("task.blocked", events)
            self.assertIn("run.blocked", events)
            # t2/t3 were never attempted.
            self.assertEqual(state.task_state("t2").state, "pending")
            self.assertEqual(state.task_state("t3").state, "pending")

    def test_resume_after_block_re_attempts_the_task(self):
        plan = make_plan()
        with tempfile.TemporaryDirectory() as data_dir:
            engine, run_id, _driver, _ = run_with_mock(
                plan, [blocked_turn(plan, "t1")], data_dir)
            from las.modules.execution import DriverConfig
            from las.modules.providers import ProviderRegistry
            from las.modules.providers.mock import MockAdapter
            registry = ProviderRegistry()
            registry.register("mock", MockAdapter([
                success_turn(plan, "t1"),
                success_turn(plan, "t2"),
                success_turn(plan, "t3"),
            ]))
            driver = engine.driver(run_id, registry,
                                   {"default": ("mock", "m")},
                                   DriverConfig(failure_policy="stop"))
            driver.run()
            state = engine.state(run_id)
            self.assertEqual(state.status, "succeeded")
            self.assertEqual(state.task_state("t1").state, "succeeded")


class BenchFixtureTests(unittest.TestCase):
    def test_every_family_has_deterministic_input_files(self):
        from bench.goals import FIXTURES, build_corpus, seed_workspace
        from las.modules.security import Workspace
        families = {g.family for g in build_corpus(100)}
        self.assertEqual(families, set(FIXTURES),
                         "every corpus family must provision inputs")
        with tempfile.TemporaryDirectory() as dir_a:
            with tempfile.TemporaryDirectory() as dir_b:
                for goal in build_corpus(100)[:20]:
                    ws_a, ws_b = Workspace(dir_a), Workspace(dir_b)
                    written_a = seed_workspace(ws_a, goal)
                    written_b = seed_workspace(ws_b, goal)
                    self.assertEqual(written_a, written_b)
                    self.assertTrue(written_a)
                    for rel in written_a:
                        self.assertEqual(ws_a.read_file(rel),
                                         ws_b.read_file(rel))

    def test_mock_corpus_reports_blocked_runs(self):
        from bench.goals import build_corpus
        from bench.harness import run_corpus
        with tempfile.TemporaryDirectory() as data_dir:
            metrics = run_corpus(data_dir, build_corpus(6))
            self.assertIn("blocked_runs", metrics)
            self.assertEqual(metrics["blocked_runs"], 0)
            self.assertEqual(metrics["run_failures"], 0)


class SourceLeniencyTests(unittest.TestCase):
    """H-06 accepts verbose file-path prefixes (measured: 'workspace
    file: a.csv' killed an otherwise perfect handoff twice)."""

    def test_verbose_prefix_resolves(self):
        import tempfile
        from las.modules.handoffs import _source_ok
        from las.modules.security import Workspace
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write_file("sales-q3-2026.csv", "region\n")
            self.assertTrue(_source_ok("sales-q3-2026.csv", ws))
            self.assertTrue(
                _source_ok("workspace file: sales-q3-2026.csv", ws))
            self.assertTrue(
                _source_ok("workspace: sales-q3-2026.csv", ws))
            self.assertTrue(_source_ok("tool:call_1", ws))
            self.assertFalse(_source_ok("workspace files", ws))
            self.assertFalse(_source_ok("", ws))


if __name__ == "__main__":
    unittest.main()
