"""Task and run state machines, rebuilt by replaying the event log (spec 5.6, 6.3).

The ``events`` table is the source of truth; nothing here writes events.
The driver (las.scheduler) owns writes.  An attempt that started but never
ended is marked ``aborted`` by the driver on restart and its task returns to
``ready``; no partial handoff is ever used (RUN-5, AC-06).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from las.shared.events_contract import Event
from las.shared.plan_contract import Plan
from las.store import Store

RUN_STATES = ("queued", "running", "paused", "succeeded", "failed", "cancelled")
TASK_STATES = ("pending", "ready", "stale", "awaiting_approval", "running",
               "succeeded", "failed", "skipped")

ACTIONABLE_STATES = ("pending", "ready", "awaiting_approval", "failed")


@dataclass
class AttemptState:
    attempt_id: str
    provider: str = ""
    model: str = ""
    status: str = "running"      # running | aborted | ended
    code: str = ""               # failure code when ended
    request_hash: str = ""

    def to_obj(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TaskState:
    task_id: str
    state: str = "pending"
    attempts: list[AttemptState] = field(default_factory=list)
    handoff: Optional[dict] = None
    gate_reason: str = ""
    gate_shown_hash: str = ""
    error_code: str = ""
    tool_calls: int = 0
    tool_denials: int = 0
    usage: dict = field(default_factory=dict)
    stale: bool = False

    def to_obj(self) -> dict:
        return {
            "task_id": self.task_id, "state": self.state,
            "attempts": [a.to_obj() for a in self.attempts],
            "handoff": self.handoff, "gate_reason": self.gate_reason,
            "error_code": self.error_code, "tool_calls": self.tool_calls,
            "tool_denials": self.tool_denials, "usage": self.usage, "stale": self.stale,
        }


@dataclass
class RunState:
    run_id: str
    plan: Plan
    plan_id: str
    plan_version_id: str
    status: str = "queued"
    title: str = ""
    caps: dict = field(default_factory=dict)
    tasks: dict[str, TaskState] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    paused_reason: str = ""
    failure_code: str = ""
    approved_gates: set[str] = field(default_factory=set)
    rejected_gates: set[str] = field(default_factory=set)
    # constraint id -> (state, released_by_task_id)
    constraints: dict[str, tuple[str, str]] = field(default_factory=dict)
    compactions: list[dict] = field(default_factory=list)

    # -- helpers -----------------------------------------------------------

    def task_state(self, task_id: str) -> Optional[TaskState]:
        return self.tasks.get(task_id)

    def next_task(self) -> Optional[str]:
        """First task in plan order that has not succeeded or been skipped (5.6 step 1)."""
        for t in self.plan.tasks:
            st = self.tasks.get(t.id)
            if st is None:
                return t.id
            if st.state in ("succeeded", "skipped"):
                continue
            if t.type in ("gate", "note"):
                continue
            return t.id
        return None

    def is_done(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")

    def tokens_used(self) -> int:
        total = 0
        for st in self.tasks.values():
            u = st.usage or {}
            total += int(u.get("tokens_in", 0)) + int(u.get("tokens_out", 0))
        return total

    def constraint_active(self, constraint_id: str) -> bool:
        entry = self.constraints.get(constraint_id)
        if entry is None:
            return True
        state, released_by = entry
        if state == "resolved":
            # A constraint resolved by a task that later went stale reopens (spec 5.10).
            if released_by:
                st = self.tasks.get(released_by)
                if st is not None and st.stale:
                    return True
            return False
        return True

    def awaiting_gate(self) -> Optional[str]:
        for tid, st in self.tasks.items():
            if st.state == "awaiting_approval":
                return tid
        return None

    def to_obj(self) -> dict:
        return {
            "run_id": self.run_id, "plan_id": self.plan_id, "status": self.status,
            "title": self.title or self.plan.title, "caps": self.caps,
            "tasks": {tid: st.to_obj() for tid, st in self.tasks.items()},
            "paused_reason": self.paused_reason, "failure_code": self.failure_code,
            "tokens_used": self.tokens_used(),
        }


def replay(store: Store, run_id: str, plan: Plan) -> RunState:
    """Rebuild run state from the event log (RUN-5)."""
    events = store.events(run_id)
    state: Optional[RunState] = None
    for ev in events:
        p = ev.payload
        if ev.type == "run.created":
            state = RunState(
                run_id=run_id, plan=plan, plan_id=str(p.get("plan_id", plan.id)),
                plan_version_id=str(p.get("plan_version_id", "")),
                status="queued", title=str(p.get("title", "")),
                caps=dict(p.get("caps") or {}))
            for t in plan.tasks:
                state.tasks[t.id] = TaskState(t.id)
        elif state is None:
            continue
        elif ev.type == "task.ready":
            _task(state, p).state = "ready"
        elif ev.type == "task.stale":
            ts = _task(state, p)
            ts.state = "stale"
            ts.stale = True
            ts.handoff = None
        elif ev.type == "gate.waiting":
            ts = _task(state, p)
            ts.state = "awaiting_approval"
            ts.gate_reason = str(p.get("reason", "user"))
            ts.gate_shown_hash = str(p.get("shown_hash", ""))
        elif ev.type == "gate.approved":
            ts = _task(state, p)
            ts.state = "ready"
            ts.gate_reason = ""
            state.approved_gates.add(ts.task_id)
        elif ev.type == "gate.rejected":
            ts = _task(state, p)
            state.rejected_gates.add(ts.task_id)
            decision = str(p.get("decision", "stop"))
            if decision == "skip":
                ts.state = "skipped"
            elif decision == "edit":
                ts.state = "ready"
            else:
                ts.state = "pending"
        elif ev.type == "attempt.started":
            ts = _task(state, p)
            ts.state = "running"
            ts.attempts.append(AttemptState(
                attempt_id=str(p.get("attempt_id", "")),
                provider=str(p.get("provider", "")),
                model=str(p.get("model", "")),
                request_hash=str(p.get("request_hash", ""))))
        elif ev.type == "attempt.aborted":
            ts = _task(state, p)
            for a in reversed(ts.attempts):
                if a.status == "running":
                    a.status = "aborted"
                    break
            if ts.state == "running":
                ts.state = "ready"
        elif ev.type == "tool.called":
            _task(state, p).tool_calls += 1
        elif ev.type == "tool.denied":
            _task(state, p).tool_denials += 1
        elif ev.type == "handoff.recorded":
            _task(state, p).handoff = dict(p.get("handoff") or {})
        elif ev.type == "handoff.edited":
            _task(state, p).handoff = dict(p.get("handoff") or {})
        elif ev.type == "task.succeeded":
            ts = _task(state, p)
            ts.state = "succeeded"
            ts.stale = False      # a fresh result replaces the stale one
            ts.usage = _sum_usage(ts.usage, p.get("usage") or {})
            _end_attempt(ts)
        elif ev.type == "task.failed":
            ts = _task(state, p)
            ts.state = "failed"
            ts.error_code = str(p.get("code", ""))
            ts.usage = _sum_usage(ts.usage, p.get("usage") or {})
            _end_attempt(ts, str(p.get("code", "")))
        elif ev.type == "task.skipped":
            _task(state, p).state = "skipped"
        elif ev.type == "constraint.released":
            state.constraints[str(p.get("constraint_id", ""))] = (
                "resolved", str(p.get("task_id", "")))
        elif ev.type == "compaction.applied":
            state.compactions.append(dict(p))
        elif ev.type == "run.paused":
            state.status = "paused"
            state.paused_reason = str(p.get("reason", ""))
        elif ev.type == "run.resumed":
            state.status = "running"
            state.paused_reason = ""
        elif ev.type == "run.cancelled":
            state.status = "cancelled"
        elif ev.type == "run.succeeded":
            state.status = "succeeded"
        elif ev.type == "run.failed":
            state.status = "failed"
            state.failure_code = str(p.get("code", ""))
    if state is None:
        raise ValueError(f"no events for run {run_id}")
    state.events = events
    # Refresh plan constraint states from releases (for task_constraints rendering).
    for c in state.plan.constraints:
        entry = state.constraints.get(c.id)
        if entry is not None:
            c.state = "resolved" if state.constraint_active(c.id) is False else "active"
    return state


def _task(state: RunState, payload: dict) -> TaskState:
    tid = str(payload.get("task_id", ""))
    ts = state.tasks.get(tid)
    if ts is None:
        ts = TaskState(tid)
        state.tasks[tid] = ts
    return ts


def _sum_usage(current: dict, added: dict) -> dict:
    out = dict(current)
    for key in ("tokens_in", "tokens_out", "duration_ms", "attempts"):
        if key in added:
            out[key] = out.get(key, 0) + (added[key] or 0)
    return out


def _end_attempt(ts: TaskState, code: str = "") -> None:
    for a in reversed(ts.attempts):
        if a.status == "running":
            a.status = "ended"
            a.code = code
            break
