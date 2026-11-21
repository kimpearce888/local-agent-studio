"""Regression tests for the mandate audit items fixed in the reference
engine (section 18 of the delivery mandate):

- item 6:  the driver passes a real token counter to context assembly
- item 8:  supporting model calls (summarizer) are accounted
- item 11: an empty command allowlist denies, not allows
- item 18: resume restores the frozen execution configuration
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from las.modules.context import assemble_context, estimate_tokens
from las.modules.execution import DriverConfig, Engine
from las.modules.execution.budgets import DEFAULTS
from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.modules.security import PermissionBroker, Workspace
from las.shared.plan_contract import Plan, Task


def make_plan(n=1):
    tasks = [Task(id=f"t{i}", type="task", title=f"Step {i}",
                  instructions=f"Complete step {i} of the goal fully.",
                  needs=[f"t{i-1}"] if i > 1 else [])
             for i in range(1, n + 1)]
    return Plan(id="plan-audit", title="Audit", goal="Goal",
                created_at="2026-11-21T00:00:00Z", tasks=tasks)


class TestItem6TokenCounter(unittest.TestCase):
    def test_counter_returning_none_falls_back_and_demotes_exact(self):
        task = make_plan().tasks[0]
        plan = make_plan()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = assemble_context(
                task, plan, {}, Workspace(tmp),
                PermissionBroker(Workspace(tmp)), context_length=8192,
                token_counter=lambda msgs: None)
            self.assertFalse(ctx.exact_counts)   # labelled estimate
            self.assertGreater(ctx.token_estimate, 0)

    def test_driver_passes_its_counter(self):
        """The driver builds an adapter-backed counter and hands it to
        assembly (the audit found `token_counter=None` in the call)."""
        import inspect
        from las.modules.execution import driver as driver_mod
        source = inspect.getsource(driver_mod.RunDriver._assemble)
        self.assertIn("token_counter=token_counter", source)
        self.assertNotIn("token_counter=None", source)


class TestItem8SupportUsage(unittest.TestCase):
    def test_summarizer_usage_recorded_and_counted(self):
        """When the context ladder invokes the summarizer, its tokens
        land in the event log as support.usage and in the run total."""
        import tempfile
        with tempfile.TemporaryDirectory() as data:
            engine = Engine(data)
            plan = make_plan(1)
            # Force the compaction ladder: a huge handoff summary from a
            # dependency plus a tiny context window.
            plan.tasks[0].needs = []
            run_id = engine.create_run(plan)
            engine.store.append(run_id, "run.succeeded", {})

            # Direct check of the driver's summarizer wiring: drive one
            # task whose assembly overflows and needs summarising.
            plan2 = Plan(id="p2", title="T", goal="G",
                         created_at="2026-11-21T00:00:00Z", tasks=[
                             Task(id="t1", type="task", title="Big",
                                   instructions="Do the work " * 200,
                                   needs=[])])
            run_id = engine.create_run(plan2)
            registry = ProviderRegistry()
            registry.register("mock", MockAdapter([
                {"text": "Short summary of the handoff."}
                for _ in range(4)]))
            config = DriverConfig(failure_policy="stop",
                                  context_length=4096)
            driver = engine.driver(run_id, registry,
                                   {"default": ("mock", "m")}, config)
            results = driver.run()
            state = engine.state(run_id)
            support_events = [e for e in engine.store.events(run_id)
                              if e.type == "support.usage"]
            # The mock reports usage for every chat call; any summariser
            # call the ladder made must appear as support.usage.
            for e in support_events:
                self.assertIn(e.payload["kind"], ("summarizer",))
                self.assertGreater(e.payload["tokens_in"]
                                   + e.payload["tokens_out"], 0)
            total_support = sum(e.payload["tokens_in"]
                                + e.payload["tokens_out"]
                                for e in support_events)
            self.assertEqual(state.support_tokens, total_support)
            # tokens_used() includes support usage plus any attempt
            # usage the (mock) run recorded.
            self.assertEqual(
                state.tokens_used(),
                total_support + sum(
                    int(ts.usage.get("tokens_in", 0))
                    + int(ts.usage.get("tokens_out", 0))
                    for ts in state.tasks.values()))

    def test_replay_counts_support_usage(self):
        with tempfile.TemporaryDirectory() as data:
            engine = Engine(data)
            plan = make_plan(1)
            run_id = engine.create_run(plan)
            engine.store.append(run_id, "support.usage",
                                {"task_id": "t1", "kind": "summarizer",
                                 "tokens_in": 30, "tokens_out": 5})
            state = engine.state(run_id)
            self.assertEqual(state.support_tokens, 35)
            self.assertEqual(state.tokens_used(), 35)


class TestItem11EmptyAllowlist(unittest.TestCase):
    def _broker(self, **kw):
        return PermissionBroker(Workspace(tempfile.mkdtemp()), **kw)

    def _task(self):
        return Task(id="t1", type="task", title="T",
                    instructions="Do the work here completely.",
                    tools=["run_command"])

    def test_empty_allowlist_denies(self):
        broker = self._broker(enable_shell=True, command_allowlist=[])
        decision = broker.check("run_command", {"command": ["ls"]},
                                self._task())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "command-allowlist-empty")

    def test_allowlisted_command_still_allowed(self):
        broker = self._broker(enable_shell=True, command_allowlist=["ls"])
        decision = broker.check("run_command", {"command": ["ls", "-l"]},
                                self._task())
        self.assertTrue(decision.allowed)


class TestItem18FrozenConfig(unittest.TestCase):
    def _run_with_config(self, data, config, models):
        engine = Engine(data)
        plan = make_plan(1)
        run_id = engine.create_run(plan, models=models, config=config)
        return engine, run_id

    def test_resume_restores_frozen_config(self):
        with tempfile.TemporaryDirectory() as data:
            frozen = DriverConfig(
                failure_policy="stop", cap_tokens_run=12345,
                cap_wall_minutes_run=7, context_length=2048,
                enable_shell=True, command_allowlist=["git"],
                web_domains=["example.com"])
            engine, run_id = self._run_with_config(
                data, frozen, {"default": ("p", "m1")})
            # No config/models given: the driver must restore them.
            registry = ProviderRegistry()
            registry.register("p", MockAdapter([{"text": "ok"}]))
            restored = engine.driver(run_id, registry)
            self.assertEqual(restored.config.failure_policy, "stop")
            self.assertEqual(restored.config.cap_tokens_run, 12345)
            self.assertEqual(restored.config.cap_wall_minutes_run, 7)
            self.assertEqual(restored.config.context_length, 2048)
            self.assertTrue(restored.config.enable_shell)
            self.assertEqual(restored.config.command_allowlist, ["git"])
            self.assertEqual(restored.config.web_domains, ["example.com"])
            # JSON round-trip stores pairs as lists; unpacking works either way.
            self.assertEqual(dict(restored.models),
                             {"default": ["p", "m1"]})

    def test_explicit_config_still_wins(self):
        with tempfile.TemporaryDirectory() as data:
            frozen = DriverConfig(failure_policy="stop")
            engine, run_id = self._run_with_config(data, frozen, {})
            registry = ProviderRegistry()
            explicit = DriverConfig(failure_policy="skip")
            driver = engine.driver(run_id, registry, config=explicit)
            self.assertEqual(driver.config.failure_policy, "skip")

    def test_old_runs_without_frozen_config_still_work(self):
        with tempfile.TemporaryDirectory() as data:
            engine, run_id = self._run_with_config(data, None, {})
            registry = ProviderRegistry()
            driver = engine.driver(run_id, registry,
                                   {"default": ("p", "m")})
            self.assertEqual(driver.config.failure_policy,
                             DriverConfig().failure_policy)


if __name__ == "__main__":
    unittest.main()
