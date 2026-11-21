"""The built-in self-test (spec S1 step 5, DST-2).

A three-task plan with hard caps (20,000 tokens, 5 minutes) that runs
against a provider and reports pass or fail per step.  With no arguments it
uses the deterministic mock provider, so `studio selftest` works offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from las.providers import ProviderRegistry
from las.providers.base import Adapter
from las.schema.plan import Constraint, Plan, Task
from las.scheduler import DriverConfig, Engine

SELFTEST_TOKEN_CAP = 20_000
SELFTEST_WALL_MINUTES = 5

SELFTEST_GOAL = (
    "Produce a one-page briefing about renewable energy trends. "
    "You must cite every figure. Never invent numbers. Only use the facts "
    "provided by earlier tasks.")


def selftest_plan() -> Plan:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = Plan(id="plan-selftest", title="Self-test: gather, draft, collect",
                goal=SELFTEST_GOAL, created_at=now,
                options={"granularity": "coarse", "max_tasks": 10, "stage_barriers": False})
    plan.constraints = [
        Constraint(id="c1", text="You must cite every figure.", state="active",
                   authority="user", scope="all", origin="goal"),
        Constraint(id="c2", text="Never invent numbers.", state="active",
                   authority="user", scope="all", origin="goal"),
    ]
    plan.tasks = [
        Task(id="t1", type="task", title="Gather facts",
             instructions="List five verifiable facts about renewable energy trends in 2024, "
                          "each with its source. Write them to facts.md.",
             needs=[], expected_output="facts.md with five sourced facts",
             constraints=["c1", "c2"], role="worker",
             tools=["write_file", "read_file", "list_files"]),
        Task(id="t2", type="task", title="Draft the briefing",
             instructions="Draft a one-page briefing using only the facts gathered by t1. "
                          "Cite every figure. Write it to draft.md.",
             needs=["t1"], expected_output="draft.md, one page",
             constraints=["c1", "c2"], role="worker",
             tools=["read_file", "write_file"]),
        Task(id="t3", type="collector", title="Collect the final result",
             instructions="Combine the facts from t1 and the draft from t2 into the final "
                          "briefing at report.md. Keep every citation.",
             needs=["t1", "t2"], expected_output="report.md, final briefing",
             constraints=["c1", "c2"], role="collector",
             tools=["read_file", "write_file"]),
    ]
    return plan


def _handoff_block(task_id: str, summary: str, constraints: list[dict],
                   deliverables: list[dict]) -> str:
    import json
    return (
        f"Work finished for {task_id}. {summary}\n"
        "<handoff>\n"
        + json.dumps({
            "task_id": task_id,
            "status": "done",
            "summary": summary,
            "key_facts": [{"fact": "Self-test fact with source", "source": "message:1"}],
            "binding_constraints": constraints,
            "decisions": [],
            "open_issues": [],
            "deliverables": deliverables,
        }, ensure_ascii=False, indent=1)
        + "\n</handoff>")


def _selftest_script(plan: Plan) -> list[dict]:
    constraints = [{"id": c.id, "text": c.text, "state": "active",
                    "authority": c.authority} for c in plan.constraints]
    return [
        {"tool_calls": [{"name": "write_file",
                         "arguments": {"path": "facts.md",
                                       "content": "1. Solar grew 30% (IEA 2024).\n"
                                                  "2. Wind capacity doubled (GWEC).\n"
                                                  "3. Battery costs fell 14% (BNEF).\n"
                                                  "4. China leads installations (IEA).\n"
                                                  "5. EU renewables passed 40% of power "
                                                  "(Ember).\n"}}]},
        {"text": _handoff_block("t1", "Gathered five sourced facts into facts.md.",
                                constraints, [{"path": "facts.md"}])},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "facts.md"}}]},
        {"tool_calls": [{"name": "write_file",
                         "arguments": {"path": "draft.md",
                                       "content": "# Briefing\nSolar grew 30% (IEA 2024). "
                                                  "Wind doubled (GWEC). Costs fell 14% (BNEF).\n"}}]},
        {"text": _handoff_block("t2", "Drafted the one-page briefing at draft.md.",
                                constraints, [{"path": "draft.md"}])},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "draft.md"}}]},
        {"tool_calls": [{"name": "write_file",
                         "arguments": {"path": "report.md",
                                       "content": "# Final briefing\nAll figures cited: "
                                                  "IEA 2024, GWEC, BNEF, Ember.\n"}}]},
        {"text": _handoff_block("t3", "Collected the final briefing at report.md.",
                                constraints, [{"path": "report.md"}])},
    ]


@dataclass
class SelfTestResult:
    ok: bool
    run_id: str = ""
    steps: list[dict] = None  # [{name, ok, detail}]
    tokens_used: int = 0
    error: str = ""

    def __post_init__(self):
        if self.steps is None:
            self.steps = []


def run_selftest(data_dir: str, adapter: Adapter | None = None) -> SelfTestResult:
    """Run the built-in plan end to end with hard caps (DST-2 step 5).

    ``providers.mock`` is an optional dependency: without an explicit
    adapter and without the mock module installed, the self-test degrades
    with an actionable message instead of failing the import.
    """
    plan = selftest_plan()
    if adapter is None:
        try:
            from las.providers.mock import MockAdapter  # optional-dep: providers.mock
        except ImportError:
            return SelfTestResult(
                ok=False,
                error="No provider available: the built-in mock provider module "
                      "is not installed. Pass --provider, or install providers.mock "
                      "(studio modules list shows installed modules).",
                steps=[{"name": "provider", "ok": False,
                        "detail": "providers.mock not installed"}])
        adapter = MockAdapter(_selftest_script(plan))
    engine = Engine(data_dir)
    registry = ProviderRegistry()
    registry.register("selftest", adapter)
    run_id = engine.create_run(plan, cap_tokens=SELFTEST_TOKEN_CAP,
                               cap_wall_minutes=SELFTEST_WALL_MINUTES, title="Self-test")
    config = DriverConfig(failure_policy="stop",
                          cap_tokens_run=SELFTEST_TOKEN_CAP,
                          cap_wall_minutes_run=SELFTEST_WALL_MINUTES)
    driver = engine.driver(run_id, registry, {"default": ("selftest", "selftest-model")},
                           config)
    result = SelfTestResult(ok=False, run_id=run_id)
    try:
        results = driver.run()
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"{type(exc).__name__}: {exc}"
        result.steps = [{"name": "run", "ok": False, "detail": result.error}]
        return result
    for r in results:
        result.steps.append({"name": r.kind, "ok": r.kind in ("task_succeeded", "run_succeeded"),
                             "detail": r.code or r.detail or r.task_id})
    state = engine.state(run_id)
    result.tokens_used = state.tokens_used()
    result.ok = state.status == "succeeded"
    if not result.ok:
        result.error = state.failure_code or state.paused_reason or state.status
    return result


__all__ = ["SelfTestResult", "run_selftest", "selftest_plan"]
