"""Approval gates and the Rule-of-Two defence (spec 5.6 step 2, BLD-4).

Private implementation file of the execution module. An approved gate is
never opened twice; a task with all three risk flags requires a gate.
"""

from __future__ import annotations

import hashlib

from las.modules.execution.budgets import StepResult
from las.modules.security import PermissionBroker
from las.modules.storage import Store, canonical_json
from las.shared.plan_contract import Task


def open_gate(store: Store, run_id: str, state_status: str, task: Task,
              reason: str, assemble) -> StepResult:
    """Pause the run at a gate for ``task`` (HIL-1).

    ``assemble`` is the driver's context assembly callable; the shown hash
    records exactly what the user will review (spec 6.3 gate.waiting).
    """
    ctx = assemble(task)
    shown_hash = hashlib.sha256(canonical_json(ctx.request_obj()).encode()).hexdigest()
    store.append(run_id, "gate.waiting", {
        "task_id": task.id, "reason": reason, "shown_hash": shown_hash})
    # A run waiting at a gate is paused until the user decides (S7).
    if state_status != "paused":
        store.append(run_id, "run.paused", {"reason": f"gate waiting: {task.id}"})
    return StepResult("gate_waiting", task.id, detail=reason)


def flags_map(broker: PermissionBroker, plan_tasks, state) -> dict:
    """Compute (A, B, C) for every task, by code, from granted tools (5.9).

    A propagates from input handoffs unless the reviewing gate was approved.
    """
    flags: dict = {}
    for t in plan_tasks:
        reviewed: set = set()
        if t.gate_before and t.id in state.approved_gates:
            reviewed = set(t.needs)
        flags[t.id] = broker.compute_flags(t, state.plan, flags, reviewed)
    return flags
