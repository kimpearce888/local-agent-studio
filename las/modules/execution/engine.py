"""Engine: creates runs and opens drivers over one store.

Private implementation file of the execution module.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time

from las.modules.execution.budgets import DEFAULTS, DriverConfig
from las.modules.execution.driver import RunDriver
from las.modules.execution.runstate import RunState, replay
from las.modules.providers import ProviderRegistry
from las.shared.plan_contract import Plan
from las.modules.storage import Store


class Engine:
    """Creates runs and opens drivers over one store."""

    def __init__(self, data_dir: str):
        self.store = Store(data_dir)

    def create_run(self, plan: Plan, cap_tokens: int | None = None,
                   cap_wall_minutes: int | None = None, title: str = "") -> str:
        run_id = "run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        caps = {
            "tokens": cap_tokens or DEFAULTS["cap_tokens_run"],
            "wall_minutes": cap_wall_minutes or DEFAULTS["cap_wall_minutes_run"],
        }
        payload = {
            "plan_id": plan.id,
            "plan_version_id": f"{plan.id}@{plan.created_at}",
            "title": title or plan.title,
            "goal_hash": hashlib.sha256(plan.goal.encode("utf-8")).hexdigest(),
            "caps": caps,
            "plan": plan.to_obj(),
        }
        self.store.save_plan(plan.to_obj())
        self.store.append(run_id, "run.created", payload)
        os.makedirs(self.store.workspace_dir(run_id), exist_ok=True)
        return run_id

    def state(self, run_id: str) -> RunState:
        events = self.store.events(run_id)
        if not events:
            raise ValueError(f"unknown run {run_id}")
        plan_obj = events[0].payload.get("plan")
        if not plan_obj:
            raise ValueError(f"run {run_id} has no plan snapshot")
        return replay(self.store, run_id, Plan.from_obj(plan_obj))

    def verify(self, run_id: str) -> bool:
        return self.store.verify(run_id)

    def edit_task(self, run_id: str, task_id: str, changes: dict,
                  reason: str = "") -> None:
        """Edit a pending task (RUN-6). The edit is an event; replay applies
        it to the plan the run executes, so it survives crashes and replays.
        Only tasks that have not started may be edited; the run must not be
        in a terminal state."""
        state = self.state(run_id)
        if state.status in ("succeeded", "failed", "cancelled"):
            raise ValueError(
                f"Run {run_id} is {state.status}; tasks can no longer be edited.")
        ts = state.tasks.get(task_id)
        if ts is None:
            raise ValueError(f"No task {task_id} in run {run_id}.")
        if ts.state != "pending":
            raise ValueError(
                f"Task {task_id} is {ts.state}; only pending tasks can be edited.")
        task = next((t for t in state.plan.tasks if t.id == task_id), None)
        edited = dict(changes)
        edited["task_id"] = task_id
        if reason:
            edited["reason"] = reason
        # Validate the edited plan: an edit may not break the plan contract
        # (for example instructions below the 20-character minimum).
        from las.modules.planquality import validate_plan
        probe = Plan.from_obj(state.plan.to_obj())
        pt = next((t for t in probe.tasks if t.id == task_id), None)
        if "title" in edited:
            pt.title = str(edited["title"])
        if "instructions" in edited:
            pt.instructions = str(edited["instructions"])
        if "model" in edited:
            pt.model = dict(edited["model"]) if edited["model"] else None
        if "params" in edited:
            pt.params = dict(edited["params"] or {})
        issues = validate_plan(probe)
        if issues:
            raise ValueError(
                "The edit would make the plan invalid: "
                + "; ".join(str(i) for i in issues))
        self.store.append(run_id, "task.edited", edited)

    def driver(self, run_id: str, registry: ProviderRegistry,
               models: dict, config: DriverConfig | None = None) -> RunDriver:
        driver = RunDriver(self.store, run_id, registry, models, config or DriverConfig())
        driver.open()
        return driver
