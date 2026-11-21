"""Reporting module tests (OBS-4, COM-4).

Owned by the ``reporting`` module: Markdown/JSON export with provenance
and the AI notice. The fixture uses a local contract-compatible scripted
adapter (not the mock provider module), so these tests run in any build.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

from las.modules.reporting import ExportOptions, export_run
from las.providers import ProviderRegistry
from las.scheduler import DriverConfig, Engine
from las.schema.plan import Constraint, Plan, Task
from las.shared.provider_contract import (
    Adapter, CapabilityRecord, StreamEvent, Usage,
)


class ScriptedAdapter(Adapter):
    """Minimal contract-compatible adapter: one scripted final text per call."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    def probe(self, model: str) -> CapabilityRecord:
        return CapabilityRecord(provider_id="scripted", model=model, reachable=True,
                                authenticated=True, models_listed=True,
                                context_length=8192)

    def list_models(self) -> list[dict]:
        return [{"id": "scripted-model"}]

    def chat(self, request):
        text = self.replies.pop(0) if self.replies else "done"
        yield StreamEvent(kind="text_delta", text=text)
        yield StreamEvent(kind="done", usage=Usage(tokens_in=10, tokens_out=10))


def make_plan() -> Plan:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = Plan(id="plan-reporting", title="Reporting test plan",
                goal="Produce a short brief. You must cite every figure.",
                created_at=now)
    plan.constraints = [
        Constraint(id="c1", text="You must cite every figure.", state="active",
                   authority="user", scope="all", origin="goal"),
    ]
    plan.tasks = [
        Task(id="t1", title="Gather", instructions="Gather five sourced facts "
             "about the topic and write them to facts.md.", needs=[],
             expected_output="facts.md", constraints=["c1"]),
        Task(id="t2", type="collector", title="Collect",
             instructions="Combine everything into the final brief at report.md.",
             needs=["t1"], expected_output="report.md", constraints=["c1"],
             role="collector"),
    ]
    return plan


def constraint_entries(plan: Plan) -> list[dict]:
    return [{"id": c.id, "text": c.text, "state": "active",
             "prerequisite": c.prerequisite, "authority": c.authority,
             "fallback": c.fallback} for c in plan.constraints]


def handoff_text(task_id: str, summary: str, plan: Plan) -> str:
    obj = {
        "task_id": task_id, "status": "done", "summary": summary,
        "key_facts": [{"fact": "The market grew 20% in 2024.", "source": "message:1"}],
        "binding_constraints": constraint_entries(plan),
        "decisions": [], "open_issues": [], "deliverables": [],
    }
    return (f"Finished {task_id}. {summary}\n<handoff>\n"
            + json.dumps(obj, ensure_ascii=False, indent=1) + "\n</handoff>")


class ReportingTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def successful_run(self) -> tuple[str, Engine]:
        plan = make_plan()
        replies = [handoff_text(t.id, f"Finished {t.id}.", plan) for t in plan.tasks]
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("scripted", ScriptedAdapter(replies))
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry,
                               {"default": ("scripted", "scripted-model")},
                               DriverConfig(failure_policy="stop"))
        results = driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded",
                         [r.kind for r in results])
        return run_id, engine


class TestExport(ReportingTestBase):
    def test_markdown_contains_provenance_and_notice(self):
        run_id, engine = self.successful_run()
        state = engine.state(run_id)
        result = export_run(state, engine.store, fmt="markdown")
        self.assertIn(run_id, result.content)
        self.assertIn("AI", result.content)
        self.assertIn("You must cite every figure.", result.content)
        self.assertIn("scripted-model", result.content)
        # Without the notice, the marker disappears.
        result2 = export_run(state, engine.store, fmt="markdown",
                             options=ExportOptions(ai_notice=False))
        self.assertNotIn("generated with the assistance of AI", result2.content)

    def test_json_bundle_round_trips(self):
        run_id, engine = self.successful_run()
        result = export_run(engine.state(run_id), engine.store, fmt="json")
        bundle = json.loads(result.content)
        self.assertEqual(bundle["run"]["run_id"], run_id)
        self.assertGreater(len(bundle["events"]), 5)
        self.assertIn("cite every figure", bundle["plan"]["goal"])

    def test_export_command_writes_file(self):
        # The export command is reporting's contributed CLI command; it works
        # without the mock provider module (its fixtures live in this file).
        run_id, _ = self.successful_run()
        from las.cli import EXIT_OK, main
        out_path = os.path.join(self.tmp.name, "report.md")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["export", run_id, "--format", "markdown",
                         "-o", out_path, "--data-dir", self.data_dir])
        self.assertEqual(code, EXIT_OK, buf.getvalue())
        with open(out_path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn(run_id, report)
        self.assertIn("AI", report)


if __name__ == "__main__":
    unittest.main()
