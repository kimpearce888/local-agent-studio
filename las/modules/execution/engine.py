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
from las.shared.plan_contract import Plan, task_from_obj
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

    def replan_target(self, run_id: str) -> tuple[list, list]:
        """(kept task ids, remaining pending tasks) for PLN-6."""
        state = self.state(run_id)
        if state.status in ("succeeded", "cancelled"):
            raise ValueError(
                f"Run {run_id} is {state.status}; there is nothing to replan.")
        kept, remaining = [], []
        for t in state.plan.tasks:
            ts = state.tasks.get(t.id)
            if ts is not None and ts.state != "pending":
                kept.append(t.id)
            else:
                remaining.append(t)
        return kept, remaining

    def apply_replan(self, run_id: str, new_tasks: list,
                     reason: str = "") -> dict:
        """Replace the pending tasks with the approved proposal (PLN-6).

        Guards: the run must not be terminal; every removed task must be
        pending (never started); user-edited pending tasks stay pinned and
        are NOT removed; the resulting plan must still validate.
        """
        state = self.state(run_id)
        if state.status in ("succeeded", "cancelled"):
            raise ValueError(
                f"Run {run_id} is {state.status}; there is nothing to replan.")
        kept, remaining = self.replan_target(run_id)
        pinned = [t for t in remaining if state.tasks.get(t.id) is not None
                  and state.tasks[t.id].edited]
        pinned_ids = {t.id for t in pinned}
        removed = [t.id for t in remaining if t.id not in pinned_ids]
        kept_tasks = [t for t in state.plan.tasks
                      if t.id in set(kept) | pinned_ids]
        from las.shared.plan_contract import Task
        new_objs = [task_from_obj(t.to_obj()) if hasattr(t, "to_obj") else
                    task_from_obj(t) for t in new_tasks]
        # New ids must not collide with kept ones.
        kept_ids = {t.id for t in kept_tasks}
        for nt in new_objs:
            if nt.id in kept_ids:
                raise ValueError(
                    f"The proposal reuses task id {nt.id}, which already "
                    f"exists in the kept part of the plan.")
        synthetic = Plan(
            id="replan-probe", title=state.plan.title, goal=state.plan.goal,
            created_at=state.plan.created_at, options=state.plan.options,
            constraints=state.plan.constraints,
            tasks=kept_tasks + new_objs)
        from las.modules.planquality import validate_plan
        issues = validate_plan(synthetic)
        if issues:
            raise ValueError(
                "The replan would make the plan invalid: "
                + "; ".join(str(i) for i in issues))
        self.store.append(run_id, "plan.replanned", {
            "removed": removed, "tasks": [t.to_obj() for t in new_objs],
            "pinned": sorted(pinned_ids), "reason": reason})
        return {"removed": removed, "pinned": sorted(pinned_ids),
                "added": [t.id for t in new_objs]}

    def list_summaries(self, status: str = "") -> list[dict]:
        """History rows for `list` (OBS-1) from the derived summary table.

        A run's summary is replayed once and cached; it refreshes only when
        new events appeared (staleness is checked by event seq). The
        replayed state stays the source of truth - this is a cache, not a
        second ledger.
        """
        rows = []
        for run_id in self.store.run_ids():
            cached = self.store.get_summary(run_id)
            if cached is None or self.store.summary_stale_seq(
                    run_id) < self.store.current_seq(run_id):
                try:
                    state = self.state(run_id)
                except (ValueError, Exception):
                    continue
                cached = {
                    "run_id": run_id,
                    "status": state.status,
                    "title": (state.title or state.plan.title)[:60],
                    "tasks_total": len(state.plan.tasks),
                    "tasks_done": sum(1 for t in state.tasks.values()
                                      if t.state == "succeeded"),
                    "tokens": state.tokens_used(),
                }
                self.store.save_summary(
                    run_id, self.store.current_seq(run_id),
                    status=cached["status"], title=cached["title"],
                    tasks_total=cached["tasks_total"],
                    tasks_done=cached["tasks_done"], tokens=cached["tokens"])
            if status and cached["status"] != status:
                continue
            rows.append(cached)
        return rows

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
