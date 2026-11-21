"""Shared test helpers: plans, handoff text, and a mock-driven run."""

from __future__ import annotations

import json
from typing import Optional

from las.providers import ProviderRegistry
from las.providers.mock import MockAdapter
from las.schema.plan import Constraint, Plan, Task
from las.scheduler import DriverConfig, Engine, RunDriver

GOAL = (
    "Produce a short market brief about electric bikes for a small retailer. "
    "You must cite every figure. Never invent numbers. Only use facts from earlier tasks. "
    "The final report requires approval before it is published.")


def make_plan(tasks: Optional[list[Task]] = None,
              constraints: Optional[list[Constraint]] = None,
              goal: str = GOAL) -> Plan:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = Plan(id="plan-test", title="Test plan", goal=goal, created_at=now,
                options={"granularity": "normal", "max_tasks": 30, "stage_barriers": False})
    plan.constraints = constraints if constraints is not None else [
        Constraint(id="c1", text="You must cite every figure.", state="active",
                   authority="user", scope="all", origin="goal"),
        Constraint(id="c2", text="Never invent numbers.", state="active",
                   authority="user", scope="all", origin="goal"),
    ]
    if tasks is None:
        plan.tasks = [
            Task(id="t1", type="task", title="Gather facts",
                 instructions="Gather five sourced facts about the e-bike market. "
                              "Write them to facts.md.",
                 needs=[], expected_output="facts.md",
                 constraints=["c1", "c2"], role="worker",
                 tools=["write_file", "read_file", "list_files"]),
            Task(id="t2", type="task", title="Draft the brief",
                 instructions="Draft the market brief from t1's facts. Write it to draft.md.",
                 needs=["t1"], expected_output="draft.md",
                 constraints=["c1", "c2"], role="worker",
                 tools=["read_file", "write_file"]),
            Task(id="t3", type="collector", title="Collect the brief",
                 instructions="Combine t1 and t2 into the final brief at report.md.",
                 needs=["t1", "t2"], expected_output="report.md",
                 constraints=["c1", "c2"], role="collector",
                 tools=["read_file", "write_file"]),
        ]
    else:
        plan.tasks = tasks
    return plan


def handoff_text(task_id: str, summary: str, constraint_objs: list[dict],
                 deliverables: Optional[list[dict]] = None,
                 status: str = "done", key_facts: Optional[list[dict]] = None,
                 extra: Optional[dict] = None) -> str:
    """Build a final message ending with a <handoff> block."""
    obj = {
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "key_facts": key_facts if key_facts is not None else [
            {"fact": "The market grew 20% in 2024.", "source": "message:1"}],
        "binding_constraints": constraint_objs,
        "decisions": [],
        "open_issues": [],
        "deliverables": deliverables or [],
    }
    if extra:
        obj.update(extra)
    return (f"Finished {task_id}. {summary}\n<handoff>\n"
            + json.dumps(obj, ensure_ascii=False, indent=1) + "\n</handoff>")


def constraint_objs(plan: Plan, state: str = "active",
                    reword: Optional[dict[str, str]] = None,
                    drop: Optional[list[str]] = None,
                    resolve_evidence: Optional[dict[str, dict]] = None) -> list[dict]:
    """Handoff constraint dicts copied from the plan (optionally corrupted)."""
    drop = drop or []
    out = []
    for c in plan.constraints:
        if c.id in drop:
            continue
        text = (reword or {}).get(c.id, c.text)
        entry = {"id": c.id, "text": text, "state": state,
                 "prerequisite": c.prerequisite, "authority": c.authority,
                 "fallback": c.fallback}
        if resolve_evidence and c.id in resolve_evidence:
            entry["state"] = "resolved"
            entry["resolution_evidence"] = resolve_evidence[c.id]
        out.append(entry)
    return out


def success_turn(plan: Plan, task_id: str, summary: str = "Done.",
                 deliverables: Optional[list[dict]] = None,
                 **kwargs) -> dict:
    """A mock turn that produces a valid handoff for a task."""
    return {"text": handoff_text(task_id, summary, constraint_objs(plan),
                                 deliverables=deliverables, **kwargs)}


def run_with_mock(plan: Plan, script: list[dict], data_dir: str,
                  config: Optional[DriverConfig] = None,
                  adapter: Optional[MockAdapter] = None,
                  pause_after: Optional[str] = None):
    """Create a run and drive it to completion with a mock provider."""
    adapter = adapter or MockAdapter(script)
    engine = Engine(data_dir)
    registry = ProviderRegistry()
    registry.register("mock", adapter)
    run_id = engine.create_run(plan)
    config = config or DriverConfig(failure_policy="stop")
    driver = engine.driver(run_id, registry, {"default": ("mock", "mock-model")}, config)
    results = driver.run(pause_after=pause_after)
    return engine, run_id, driver, results


def request_json_for(data_dir: str, run_id: str, task_id: str) -> list[dict]:
    """All stored request.json payloads for a task (one per attempt)."""
    import glob
    import os
    pattern = os.path.join(data_dir, "runs", run_id, "attempts", task_id, "*", "request.json")
    out = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out
