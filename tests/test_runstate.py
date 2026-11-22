"""RUN-5 replay: the incremental form must equal the full replay."""

import tempfile
import unittest

from las.modules.execution import DriverConfig, Engine
from las.modules.execution.runstate import replay
from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.shared.plan_contract import Plan, Task

from bench.harness import handoff_text


def chain_plan(n=4):
    tasks = [Task(id=f"t{i}", type="task", title=f"Step {i}",
                  instructions=f"Complete step {i}.",
                  needs=[f"t{i-1}"] if i > 1 else [])
             for i in range(1, n + 1)]
    tasks[-1].type = "collector"
    return Plan(id="plan-r", title="Replay test", goal="Replay test",
                created_at="2026-11-21T00:00:00Z", tasks=tasks)


class TestIncrementalReplay(unittest.TestCase):
    def test_incremental_equals_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = chain_plan(4)
            engine = Engine(tmp)
            run_id = engine.create_run(plan)
            script = [{"text": handoff_text(t.id, plan)} for t in plan.tasks]
            registry = ProviderRegistry()
            registry.register("mock", MockAdapter(script))
            # Drive two tasks, abandon, resume - both replay styles touched.
            driver = engine.driver(run_id, registry,
                                   {"default": ("mock", "mock-model")},
                                   DriverConfig(failure_policy="stop"))
            driver.step(); driver.step()
            mid = replay(engine.store, run_id, plan)          # full
            self.assertFalse(mid.is_done())                   # mid-run
            engine.driver(run_id, registry, {"default": ("mock", "mock-model")},
                          DriverConfig(failure_policy="stop")).run()

            full = replay(engine.store, run_id, plan)
            # Incremental: replay in two cursor chunks from scratch.
            staged = replay(engine.store, run_id, plan, after=0)
            while staged.seq < full.seq:
                staged = replay(engine.store, run_id, plan,
                                state=staged, after=staged.seq)
            self.assertEqual(staged.seq, full.seq)
            self.assertEqual(staged.status, full.status)
            self.assertEqual(
                {k: v.state for k, v in staged.tasks.items()},
                {k: v.state for k, v in full.tasks.items()})
            self.assertEqual(
                {k: v.usage for k, v in staged.tasks.items()},
                {k: v.usage for k, v in full.tasks.items()})
            self.assertEqual(
                {k: v.handoff for k, v in staged.tasks.items()},
                {k: v.handoff for k, v in full.tasks.items()})
            self.assertEqual(staged.approved_gates, full.approved_gates)
            self.assertEqual(staged.constraints, full.constraints)


if __name__ == "__main__":
    unittest.main()


class TestRunOnFailedRun(unittest.TestCase):
    """Regression (found by the real-model worker benchmark): run()
    over an ALREADY-failed run must return immediately.

    step() used to return the raw status "failed" as the result kind,
    which run()'s break list does not contain (it expects
    "run_failed") - so run() spun forever appending StepResults until
    the OOM killer took the process at ~3GB rss.  The resume driver
    in the kill/resume benchmark hits exactly this path whenever the
    live model fails a task before the kill."""

    def test_run_over_failed_run_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = chain_plan(3)
            engine = Engine(tmp)
            run_id = engine.create_run(plan)
            garbage = [{"text": f"rambling turn {i}, no handoff block"}
                       for i in range(4)]
            registry = ProviderRegistry()
            registry.register("mock", MockAdapter(garbage))
            cfg = DriverConfig(failure_policy="stop")
            driver = engine.driver(run_id, registry,
                                   {"default": ("mock", "mock-model")}, cfg)
            results = driver.run()               # t1 fails -> run stops
            self.assertIn(results[-1].kind, ("task_failed", "run_failed"))
            self.assertEqual(engine.state(run_id).status, "failed")
            # The kill path: a fresh (resume) driver runs the failed run.
            resume = engine.driver(run_id, registry,
                                   {"default": ("mock", "mock-model")}, cfg)
            resumed = resume.run()               # used to spin forever
            self.assertEqual(resumed[-1].kind, "run_failed")
            self.assertEqual(resume.state.status, "failed")
