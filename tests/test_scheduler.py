"""Scheduler acceptance scenarios (spec section 10).

AC-04 isolation canary · AC-05 constraint preservation · AC-06 crash resume ·
AC-07 caps · AC-08 Rule of Two gate · AC-13 rerun from here, plus failure
policies, gates, loops, stalls, provider retries and exports.
"""

import json
import os
import unittest

from las.modules.providers.mock import MockAdapter, SimulatedCrash
from las.modules.execution import DriverConfig, Engine
from tests.util import (
    constraint_objs, handoff_text, make_plan, request_json_for, run_with_mock,
    success_turn,
)


class SchedulerTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def script_for(self, plan, **overrides):
        """One successful turn per task."""
        return [success_turn(plan, t.id) for t in plan.tasks]


class TestHappyPath(SchedulerTestBase):
    def test_three_task_chain_succeeds(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir)
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        self.assertEqual([r.kind for r in results],
                         ["task_succeeded"] * 3 + ["run_succeeded"])
        for t in plan.tasks:
            self.assertEqual(state.task_state(t.id).state, "succeeded")
        # Handoffs recorded and readable.
        self.assertEqual(state.task_state("t2").handoff["task_id"], "t2")
        # Event chain verifies.
        self.assertTrue(engine.verify(run_id))

    def test_workspace_shared_across_tasks(self):
        plan = make_plan()
        script = [
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "facts.md", "content": "fact 1"}}]},
            {"text": handoff_text("t1", "Wrote facts.md.",
                                  constraint_objs(plan),
                                  deliverables=[{"path": "facts.md"}])},
            {"tool_calls": [{"name": "read_file", "arguments": {"path": "facts.md"}}]},
            {"text": handoff_text("t2", "Read the facts.",
                                  constraint_objs(plan))},
            {"text": handoff_text("t3", "Collected.", constraint_objs(plan))},
        ]
        engine, run_id, driver, results = run_with_mock(plan, script, self.data_dir)
        self.assertEqual(engine.state(run_id).status, "succeeded")
        ws = os.path.join(self.data_dir, "runs", run_id, "workspace", "facts.md")
        with open(ws, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "fact 1")
        # The engine filled the missing deliverable hash (H-07).
        handoff = engine.state(run_id).task_state("t1").handoff
        self.assertEqual(len(handoff["deliverables"][0]["sha256"]), 64)


class TestIsolation(SchedulerTestBase):
    def test_ac04_canary_never_reaches_unrelated_task(self):
        plan = make_plan()
        # t3 depends only on t2, so t1's output must never reach it.
        plan.tasks[2].needs = ["t2"]
        canary = "CANARY-8f3a91"
        script = [
            {"text": handoff_text("t1", f"The secret code is {canary}.",
                                  constraint_objs(plan))},
            # t2 needs t1 -> its context legitimately contains the canary...
            {"text": handoff_text("t2", "Drafted without repeating secrets.",
                                  constraint_objs(plan))},
            # ...but t3 only needs t2, so its request must not contain it.
            {"text": handoff_text("t3", "Collected.", constraint_objs(plan))},
        ]
        engine, run_id, driver, results = run_with_mock(plan, script, self.data_dir)
        self.assertEqual(engine.state(run_id).status, "succeeded")
        t2_request = request_json_for(self.data_dir, run_id, "t2")[0]
        t3_request = request_json_for(self.data_dir, run_id, "t3")[0]
        joined_t2 = json.dumps(t2_request)
        joined_t3 = json.dumps(t3_request)
        self.assertIn(canary, joined_t2)      # dependency carries it
        self.assertNotIn(canary, joined_t3)   # isolation holds (AC-04)

    def test_ac04_canary_present_when_needed(self):
        plan = make_plan()
        canary = "CANARY-8f3a91"
        script = [
            {"text": handoff_text("t1", f"The secret code is {canary}.",
                                  constraint_objs(plan))},
            {"text": handoff_text("t2", "ok", constraint_objs(plan))},
            {"text": handoff_text("t3", "ok", constraint_objs(plan))},
        ]
        engine, run_id, driver, results = run_with_mock(plan, script, self.data_dir)
        t3_request = request_json_for(self.data_dir, run_id, "t3")[0]
        # t3 needs t1, so the canary is carried into its context.
        self.assertIn(canary, json.dumps(t3_request))


class TestConstraintPreservation(SchedulerTestBase):
    def test_ac05_reworded_constraint_repaired_once(self):
        plan = make_plan()
        good = constraint_objs(plan)
        reworded = constraint_objs(plan, reword={"c2": "Never fabricate numbers."})
        script = [
            {"text": handoff_text("t1", "Gathered.", reworded)},   # fails H-03
            {"text": handoff_text("t1", "Gathered.", good)},       # repair succeeds
            {"text": handoff_text("t2", "ok", good)},
            {"text": handoff_text("t3", "ok", good)},
        ]
        engine, run_id, driver, results = run_with_mock(plan, script, self.data_dir)
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        self.assertEqual(state.task_state("t1").state, "succeeded")
        # The recorded handoff carries the constraint verbatim.
        self.assertEqual(state.task_state("t1").handoff["binding_constraints"][1]["text"],
                         "Never invent numbers.")

    def test_ac05_second_failure_ends_attempt(self):
        plan = make_plan()
        reworded = constraint_objs(plan, reword={"c2": "Never fabricate numbers."})
        script = [
            {"text": handoff_text("t1", "Gathered.", reworded)},
            {"text": handoff_text("t1", "Gathered.", reworded)},   # repair fails too
        ]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir, DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.task_state("t1").error_code, "HANDOFF-INVALID")
        self.assertEqual(state.failure_code, "HANDOFF-INVALID")

    def test_constraints_travel_to_downstream_context(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir)
        t3_request = request_json_for(self.data_dir, run_id, "t3")[0]
        system = t3_request["messages"][0]["content"]
        self.assertIn("Never invent numbers.", system)


class TestResume(SchedulerTestBase):
    def test_ac06_crash_mid_task_resumes_without_redoing(self):
        plan = make_plan()
        adapter = MockAdapter([
            success_turn(plan, "t1"),
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
            {"crash": True},                       # dies during t4
            success_turn(plan, "t4"),
        ])
        from las.modules.providers import ProviderRegistry
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("mock", adapter)
        # A fourth task so the crash lands mid-run.
        from las.shared.plan_contract import Task
        plan.tasks.append(Task(
            id="t4", type="task", title="Polish",
            instructions="Polish the final brief and save it again.",
            needs=["t3"], constraints=["c1", "c2"]))
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry, {"default": ("mock", "m")},
                               DriverConfig(failure_policy="stop"))
        with self.assertRaises(SimulatedCrash):
            driver.run()

        # A fresh engine over the same store recovers and finishes (AC-06).
        # open() aborts the orphaned attempt and returns the task to ready.
        engine2 = Engine(self.data_dir)
        driver2 = engine2.driver(run_id, registry, {"default": ("mock", "m")},
                                 DriverConfig(failure_policy="stop"))
        state = engine2.state(run_id)
        self.assertEqual(state.task_state("t1").state, "succeeded")
        self.assertEqual(state.task_state("t3").state, "succeeded")
        self.assertEqual(state.task_state("t4").state, "ready")
        self.assertEqual(len(state.task_state("t4").attempts), 1)
        self.assertEqual(state.task_state("t4").attempts[0].status, "aborted")

        results = driver2.run()
        state = engine2.state(run_id)
        self.assertEqual(state.status, "succeeded")
        # Tasks 1-3 were not rerun: still exactly one attempt each.
        for tid in ("t1", "t2", "t3"):
            self.assertEqual(len(state.task_state(tid).attempts), 1, tid)
        # t4 restarted as a NEW attempt.
        self.assertEqual(len(state.task_state("t4").attempts), 2)
        self.assertEqual(state.task_state("t4").attempts[1].status, "ended")
        # The hash chain still verifies.
        self.assertTrue(engine2.verify(run_id))

    def test_no_partial_handoff_used_after_crash(self):
        plan = make_plan()
        adapter = MockAdapter([
            # t1 emits a *valid* handoff, then crashes before task.succeeded
            # is written?  Simulate: handoff turn then crash on the same task.
            success_turn(plan, "t1"),
            {"crash": True},      # dies during t2
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
        ])
        from las.modules.providers import ProviderRegistry
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("mock", adapter)
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry, {"default": ("mock", "m")},
                               DriverConfig(failure_policy="stop"))
        with self.assertRaises(SimulatedCrash):
            driver.run()
        state = engine.state(run_id)
        # t2 never recorded a handoff; downstream t3 must not see one.
        self.assertIsNone(state.task_state("t2").handoff)
        driver2 = engine.driver(run_id, registry, {"default": ("mock", "m")},
                                DriverConfig(failure_policy="stop"))
        driver2.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")


class TestCaps(SchedulerTestBase):
    def test_ac07_run_stops_at_token_cap(self):
        plan = make_plan()
        script = [
            {"text": handoff_text("t1", "ok", constraint_objs(plan)),
             "usage": [6000, 500]},
            {"text": handoff_text("t2", "ok", constraint_objs(plan)),
             "usage": [6000, 500]},     # this call crosses the 10,000 cap
        ]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir,
            DriverConfig(failure_policy="stop", cap_tokens_run=10_000))
        state = engine.state(run_id)
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.failure_code, "CAP-REACHED")
        # At most one model call beyond the cap: t1 succeeded, t2's single
        # call crossed it and the run stopped immediately.
        self.assertEqual(state.task_state("t1").state, "succeeded")
        self.assertEqual(state.task_state("t2").state, "failed")
        self.assertEqual(len(state.task_state("t2").attempts), 1)

    def test_task_turn_cap(self):
        plan = make_plan()
        # One *distinct* tool call per turn for 30 turns: the turn cap (25)
        # stops the task before any loop detection can apply.
        script = [{"tool_calls": [{"name": "list_files",
                                   "arguments": {"prefix": f"dir{i}"}}]}
                  for i in range(30)]
        script += [success_turn(plan, "t2"), success_turn(plan, "t3")]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir, DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.task_state("t1").error_code, "CAP-REACHED")


class TestGates(SchedulerTestBase):
    def test_gate_before_task_pauses_run(self):
        plan = make_plan()
        plan.tasks[1].gate_before = True
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir,
            DriverConfig(failure_policy="stop"))
        kinds = [r.kind for r in results]
        self.assertIn("gate_waiting", kinds)
        state = engine.state(run_id)
        self.assertEqual(state.task_state("t2").state, "awaiting_approval")
        self.assertEqual(state.task_state("t2").gate_reason, "user")
        self.assertEqual(state.status, "paused")
        # Approval lets the run continue.
        driver.approve_gate("t2", reason="looks good")
        results2 = driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")
        self.assertIn("t2", engine.state(run_id).approved_gates)

    def test_ac08_rule_of_two_gate_when_three_flags(self):
        plan = make_plan()
        plan.tasks[0].tools = ["read_file", "web_fetch"]
        plan.tasks[1].tools = ["read_file", "run_command"]
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir,
            DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        # t2 has three of three flags and no gate -> the scheduler gates it.
        self.assertEqual(state.task_state("t2").state, "awaiting_approval")
        self.assertEqual(state.task_state("t2").gate_reason, "rule_of_two")
        driver.approve_gate("t2", reason="I reviewed the tools")
        driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")

    def test_gate_rejection_stops_run(self):
        plan = make_plan()
        plan.tasks[1].gate_before = True
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir,
            DriverConfig(failure_policy="stop"))
        driver.reject_gate("t2", decision="stop", reason="no")
        state = engine.state(run_id)
        self.assertEqual(state.status, "cancelled")


class TestFailurePolicies(SchedulerTestBase):
    def bad_script(self, plan):
        return [
            {"text": "I refuse to produce a handoff block."},   # nudge 1
            {"text": "Still no handoff."},                      # nudge 2
            {"text": "No handoff again."},                      # repair
            {"text": "And none now."},                          # repair fails
        ]

    def test_policy_stop_fails_run(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, self.bad_script(plan), self.data_dir,
            DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.task_state("t1").error_code, "HANDOFF-INVALID")

    def test_policy_ask_pauses_then_retry_recovers(self):
        plan = make_plan()
        adapter = MockAdapter(self.bad_script(plan) + [
            success_turn(plan, "t1"), success_turn(plan, "t2"), success_turn(plan, "t3")])
        from las.modules.providers import ProviderRegistry
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("mock", adapter)
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry, {"default": ("mock", "m")},
                               DriverConfig(failure_policy="ask"))
        results = driver.run()
        self.assertEqual(results[-1].kind, "task_failed")
        self.assertEqual(engine.state(run_id).status, "paused")
        # The user fixes nothing but retries: the next turns succeed.
        driver.retry("t1")
        results = driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")

    def test_policy_skip_cascades_to_dependents(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, self.bad_script(plan), self.data_dir,
            DriverConfig(failure_policy="skip"))
        state = engine.state(run_id)
        self.assertEqual(state.task_state("t1").state, "skipped")
        # t2 and t3 depend on t1 -> skipped as blocked-by-skip.
        self.assertEqual(state.task_state("t2").state, "skipped")
        self.assertEqual(state.task_state("t3").state, "skipped")
        self.assertEqual(state.status, "succeeded")

    def test_provider_error_retried_with_backoff(self):
        plan = make_plan()
        recorded_sleeps = []
        script = [
            {"error": "SERVER_ERROR"},
            success_turn(plan, "t1"),
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
        ]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir,
            DriverConfig(failure_policy="stop", sleeper=recorded_sleeps.append))
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        self.assertEqual(len(state.task_state("t1").attempts), 2)
        self.assertEqual(recorded_sleeps, [2.0])

    def test_loop_detected_fails_after_retry(self):
        plan = make_plan()
        loop = {"tool_calls": [{"name": "list_files", "arguments": {}}],
                "repeat": 3}
        script = [loop, loop]        # first attempt loops, retry loops again
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir, DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        self.assertEqual(state.task_state("t1").error_code, "LOOP-DETECTED")
        self.assertEqual(state.status, "failed")

    def test_permission_denied_after_second_denial(self):
        plan = make_plan()
        plan.tasks[0].tools = ["read_file"]      # shell not granted
        script = [
            {"tool_calls": [{"name": "run_command",
                             "arguments": {"command": ["ls"]}}]},
            {"tool_calls": [{"name": "run_command",
                             "arguments": {"command": ["ls"]}}]},
        ]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir, DriverConfig(failure_policy="stop"))
        state = engine.state(run_id)
        self.assertEqual(state.task_state("t1").error_code, "PERMISSION-DENIED")
        # Both denials were audited.
        denials = [e for e in engine.store.events(run_id) if e.type == "tool.denied"]
        self.assertEqual(len(denials), 2)
        self.assertEqual(denials[0].payload["rule"], "not-in-allowlist")

    def test_text_tool_protocol_used_when_no_native_tools(self):
        plan = make_plan()
        script = [
            {"text": 'Let me write the file.\n<tool_call>{"name": "write_file", '
                     '"arguments": {"path": "facts.md", "content": "via text protocol"'
                     '}}</tool_call>'},
            {"text": handoff_text("t1", "Used the text protocol.",
                                  constraint_objs(plan))},
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
        ]
        engine, run_id, driver, results = run_with_mock(
            plan, script, self.data_dir, DriverConfig(failure_policy="stop",
                                                      tool_calling=False))
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        ws = os.path.join(self.data_dir, "runs", run_id, "workspace", "facts.md")
        with open(ws, encoding="utf-8") as fh:
            self.assertIn("via text protocol", fh.read())
        # The protocol text was injected into the system block.
        req = request_json_for(self.data_dir, run_id, "t1")[0]
        self.assertIn("TEXT TOOL PROTOCOL", req["messages"][0]["content"])


class TestRerun(SchedulerTestBase):
    def test_ac13_rerun_marks_downstream_stale_and_reopens_constraints(self):
        plan = make_plan()
        # c1 may be resolved by a tool call (H-05 authority tool:read_file).
        plan.constraints[0].authority = "tool:read_file"
        script = [
            {"tool_calls": [{"name": "read_file",
                             "arguments": {"path": "facts.md"}}]},
            {"text": handoff_text("t1", "Read and resolved c1.",
                                  constraint_objs(plan, resolve_evidence={
                                      "c1": {"source": "tool:read_file", "ref": "facts.md"}}))},
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
            # After rerun from t1: the tool call and a fresh handoff.
            {"tool_calls": [{"name": "read_file",
                             "arguments": {"path": "facts.md"}}]},
            {"text": handoff_text("t1", "Read again.",
                                  constraint_objs(plan, resolve_evidence={
                                      "c1": {"source": "tool:read_file", "ref": "facts.md"}}))},
            success_turn(plan, "t2"),
            success_turn(plan, "t3"),
        ]
        engine, run_id, driver, results = run_with_mock(plan, script, self.data_dir)
        self.assertEqual(engine.state(run_id).status, "succeeded")
        # c1 was released during t1.
        state = engine.state(run_id)
        self.assertFalse(state.constraint_active("c1"))

        # Rerun from t1 (AC-13): t1..t3 become stale, then pending.
        stale = driver.rerun_from("t1")
        self.assertEqual(stale, ["t1", "t2", "t3"])
        state = engine.state(run_id)
        for tid in ("t1", "t2", "t3"):
            self.assertEqual(state.task_state(tid).state, "ready")
            self.assertTrue(state.task_state(tid).stale)
            # Old attempts stay in history.
            self.assertEqual(len(state.task_state(tid).attempts), 1)
        # The constraint resolved by the now-stale t1 reopens.
        self.assertTrue(state.constraint_active("c1"))

        # The scheduler restarts at t1 and finishes.
        driver.run()
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        self.assertEqual(len(state.task_state("t1").attempts), 2)
        self.assertFalse(state.constraint_active("c1"))


class TestEventChain(SchedulerTestBase):
    def test_event_types_present(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, self.script_for(plan), self.data_dir)
        types = [e.type for e in engine.store.events(run_id)]
        for expected in ("run.created", "attempt.started", "message.added",
                         "handoff.recorded", "task.succeeded", "run.succeeded"):
            self.assertIn(expected, types)
        # Every event carries the chain fields.
        for e in engine.store.events(run_id):
            self.assertEqual(len(e.prev_hash), 64)
            self.assertEqual(len(e.hash), 64)
            self.assertTrue(e.ts.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
