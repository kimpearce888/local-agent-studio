"""Run and task controls (RUN-6, spec 5.10).

Private implementation file of the execution module. Controls append the
authoritative events; the driver refetches state afterwards.
"""

from __future__ import annotations

from las.modules.storage import Store
from las.modules.execution.runstate import RunState


def approve_gate(store: Store, run_id: str, state: RunState, task_id: str,
                 reason: str) -> None:
    ts = state.task_state(task_id)
    if ts is None or ts.state != "awaiting_approval":
        raise ValueError(f"task {task_id} is not waiting at a gate")
    store.append(run_id, "gate.approved", {
        "task_id": task_id, "shown_hash": ts.gate_shown_hash,
        "decision": "approve", "reason": reason})
    if state.status == "paused":
        store.append(run_id, "run.resumed", {"reason": f"gate approved: {task_id}"})


def reject_gate(store: Store, run_id: str, state: RunState, task_id: str,
                decision: str = "stop", reason: str = "") -> None:
    if decision not in ("stop", "skip", "edit"):
        raise ValueError("decision must be stop, skip or edit")
    ts = state.task_state(task_id)
    if ts is None or ts.state != "awaiting_approval":
        raise ValueError(f"task {task_id} is not waiting at a gate")
    store.append(run_id, "gate.rejected", {
        "task_id": task_id, "shown_hash": ts.gate_shown_hash,
        "decision": decision, "reason": reason})
    if decision == "stop":
        store.append(run_id, "run.cancelled", {"reason": f"gate rejected: {task_id}"})
    elif state.status == "paused":
        store.append(run_id, "run.resumed",
                     {"reason": f"gate rejected ({decision}): {task_id}"})


def retry(store: Store, run_id: str, state: RunState, task_id: str) -> None:
    ts = state.task_state(task_id)
    if ts is None or ts.state != "failed":
        raise ValueError(f"task {task_id} is not failed")
    store.append(run_id, "task.ready", {"task_id": task_id})
    if state.status in ("paused", "failed"):
        store.append(run_id, "run.resumed", {"reason": f"retry {task_id}"})


def skip(store: Store, run_id: str, state: RunState, task_id: str,
         cascade: bool = True) -> list[str]:
    """Skip a task; dependent tasks are marked skipped too (spec 5.7)."""
    skipped = [task_id]
    store.append(run_id, "task.skipped", {"task_id": task_id, "code": "user-skip"})
    if cascade:
        for t in state.plan.tasks:
            if t.id == task_id:
                continue
            if any(dep in skipped for dep in t.needs):
                if state.task_state(t.id).state not in ("succeeded", "skipped"):
                    store.append(run_id, "task.skipped", {
                        "task_id": t.id, "code": "blocked-by-skip"})
                    skipped.append(t.id)
    if state.status in ("paused", "failed"):
        store.append(run_id, "run.resumed", {"reason": f"skip {task_id}"})
    return skipped


def rerun_from(store: Store, run_id: str, state: RunState, task_id: str) -> list[str]:
    """Rerun from here (spec 5.10): N..last go stale, then pending."""
    plan = state.plan
    idx = plan.order_index(task_id)
    if idx < 0:
        raise ValueError(f"unknown task {task_id}")
    stale: list[str] = []
    for t in plan.tasks[idx:]:
        if state.task_state(t.id).state in ("succeeded", "failed", "skipped",
                                            "stale", "ready", "pending"):
            store.append(run_id, "task.stale", {"task_id": t.id})
            store.append(run_id, "task.ready", {"task_id": t.id})
            stale.append(t.id)
    if state.status in ("paused", "failed", "succeeded"):
        store.append(run_id, "run.resumed", {"reason": f"rerun from {task_id}"})
    return stale
