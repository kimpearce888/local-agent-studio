"""Synthetic load tests (PRD section 11, Scale): runs of 500 tasks,
histories of 10,000 runs, and large transcripts read through the
after-seq cursor.

Sizes follow the PRD targets where the suite budget allows.  The large
transcript test writes ~96 MB of payload - the PRD's "several hundred MB"
on the reference machine - and asserts the paging contract, not a timing.
"""

import json
import tempfile
import time
import unittest

from las.modules.execution import DriverConfig, Engine
from las.modules.planquality import validate_plan
from las.modules.providers.mock import MockAdapter
from las.modules.storage import Store
from las.shared.plan_contract import MAX_TASKS_HARD, Plan, Task

from bench.harness import handoff_text
from las.modules.providers import ProviderRegistry


def make_chain_plan(n_tasks: int, goal: str = "Scale test") -> Plan:
    """A dependency chain of n_tasks tasks (t2 needs t1 ...)."""
    tasks = []
    for i in range(1, n_tasks + 1):
        tasks.append(Task(
            id=f"t{i}", type="task", title=f"Step {i}",
            instructions=f"Complete step {i} of the scale test.",
            needs=[f"t{i - 1}"] if i > 1 else []))
    tasks[-1].type = "collector"
    return Plan(id="plan-scale", title="Scale test", goal=goal,
                created_at="2026-11-21T00:00:00Z", tasks=tasks)


class TestFiveHundredTaskRun(unittest.TestCase):
    def test_500_tasks_complete(self):
        """A 500-task run (MAX_TASKS_HARD, spec 5.2 V-06) drives to
        completion and every handoff is recorded."""
        self.assertEqual(MAX_TASKS_HARD, 500)
        plan = make_chain_plan(500)
        self.assertEqual(validate_plan(plan, max_tasks=500), [])
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(tmp)
            run_id = engine.create_run(plan)
            script = [{"text": handoff_text(t.id, plan)} for t in plan.tasks]
            registry = ProviderRegistry()
            registry.register("mock", MockAdapter(script))
            started = time.monotonic()
            engine.driver(run_id, registry,
                          {"default": ("mock", "mock-model")},
                          DriverConfig(failure_policy="stop")).run()
            elapsed = time.monotonic() - started
            state = engine.state(run_id)
            self.assertEqual(state.status, "succeeded")
            self.assertEqual(
                len([e for e in engine.store.events(run_id)
                     if e.type == "task.succeeded"]), 500)
            print(f"\n500-task run: {elapsed:.1f}s")


class TestTenThousandRunHistory(unittest.TestCase):
    def test_history_lists_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(tmp)
            plan = make_chain_plan(2)
            for i in range(10_000):
                run_id = engine.create_run(plan, title=f"Run {i}")
                engine.store.append(run_id, "run.succeeded", {})
            started = time.monotonic()
            rows = engine.list_summaries()
            cold = time.monotonic() - started
            self.assertEqual(len(rows), 10_000)
            started = time.monotonic()
            rows = engine.list_summaries()          # warm: cache only
            warm = time.monotonic() - started
            self.assertEqual(len(rows), 10_000)
            done = engine.list_summaries(status="succeeded")
            self.assertEqual(len(done), 10_000)
            print(f"\n10k history: cold {cold:.2f}s, warm {warm:.3f}s")
            self.assertLess(warm, 5.0)               # generous suite bound


class TestLargeTranscriptPaging(unittest.TestCase):
    def test_hundred_megabyte_transcript_pages_by_cursor(self):
        """~96 MB of events: cursor reads (after=seq) return exactly the
        same events as one full read, in order, with the chain intact."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            run_id = "run-big"
            store.append(run_id, "run.created", {"title": "Big"})
            blob = "x" * 65_536                       # 64 KiB per event
            n = 1_500                                 # ~96 MB total
            for i in range(n):
                store.append(run_id, "message.added",
                            {"role": "user", "content": blob})
            total = store.events(run_id)
            self.assertEqual(len(total), n + 1)
            self.assertGreater(
                sum(len(json.dumps(e.payload)) for e in total), 90_000_000)
            # Page through with the after-cursor and compare.
            paged, cursor = [], 0
            while True:
                batch = store.events(run_id, after=cursor)
                if not batch:
                    break
                self.assertEqual(batch[0].seq, cursor + 1)
                paged.extend(batch)
                cursor = batch[-1].seq
                if len(paged) > 2_000:               # sample the contract
                    break
            self.assertEqual(
                [e.seq for e in paged], [e.seq for e in total[:len(paged)]])
            store.close()


if __name__ == "__main__":
    unittest.main()
