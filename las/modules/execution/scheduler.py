"""The sequential scheduler (spec 5.6, 5.7, 5.8, 5.10).

Exactly one session is active per run, in plan order (RUN-2).  Every state
change is written to the hash-chained event log *before* the next action
(RUN-5).  The driver is resumable: an attempt that started but never ended
is marked aborted on open, its task returns to ready, and no partial handoff
is ever used (AC-06).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from las.modules.context import (
    AssembledContext, TEXT_TOOL_PROTOCOL, assemble_context, estimate_tokens,
    parse_text_tool_call,
)
from las.shared.provider_contract import Adapter, ChatRequest, Message, ProviderError, SimulatedCrash, ToolCall
from las.modules.providers import ProviderRegistry
from las.modules.execution import RunState, replay
from las.modules.handoffs import EvidenceContext, Handoff, extract_handoff, validate_handoff
from las.shared.plan_contract import Plan, Task
from las.modules.storage import Store, TamperedLogError, canonical_json
from las.modules.security import PermissionBroker

# Default budgets (spec 5.8).  There is no unlimited setting.
DEFAULTS = {
    "max_output_tokens": 4096,
    "wall_task_seconds": 1800,
    "stall_timeout": 120.0,
    "max_turns": 25,
    "max_tool_calls": 100,
    "retries": 2,
    "cap_tokens_run": 2_000_000,
    "cap_wall_minutes_run": 480,
    "context_length": 8192,
}
BACKOFF_SCHEDULE = (2.0, 8.0, 30.0)   # spec 5.7, server/network errors

NO_RETRY_CODES = {"RUN-CONTEXT-OVERFLOW", "MODEL-UNAVAILABLE", "CAP-REACHED",
                  "PERMISSION-DENIED", "TOOL-CALL-INVALID", "HANDOFF-INVALID"}


class StallError(Exception):
    pass


class PauseRequested(Exception):
    pass


class CancelRequested(Exception):
    pass


@dataclass
class StepResult:
    kind: str                      # task_succeeded | task_failed | task_skipped |
                                   # gate_waiting | run_succeeded | run_failed |
                                   # paused | cancelled
    task_id: str = ""
    code: str = ""
    detail: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class DriverConfig:
    failure_policy: str = "ask"            # ask | stop | skip
    cap_tokens_run: int = DEFAULTS["cap_tokens_run"]
    cap_wall_minutes_run: int = DEFAULTS["cap_wall_minutes_run"]
    stall_timeout: float = DEFAULTS["stall_timeout"]
    context_length: int = DEFAULTS["context_length"]
    tool_calling: bool = True              # capability: native tool calls
    enable_shell: bool = False
    enable_web: bool = False
    command_allowlist: list = field(default_factory=list)
    web_domains: list = field(default_factory=list)
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass
class AttemptOutcome:
    ok: bool = False
    code: str = ""
    kind: str = ""                      # ok | provider_error | stall | loop | terminal | control
    attempt_id: str = ""
    handoff: Optional[Handoff] = None
    final_text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


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

    def driver(self, run_id: str, registry: ProviderRegistry,
               models: dict, config: DriverConfig | None = None) -> "RunDriver":
        driver = RunDriver(self.store, run_id, registry, models, config or DriverConfig())
        driver.open()
        return driver


class RunDriver:
    """Executes one task attempt per :meth:`step` (spec 5.6)."""

    def __init__(self, store: Store, run_id: str, registry: ProviderRegistry,
                 models: dict, config: DriverConfig):
        self.store = store
        self.run_id = run_id
        self.registry = registry
        # models: role -> (provider_id, model_id); also "default"
        self.models = models
        self.config = config
        self.state: Optional[RunState] = None
        self._pause_after: Optional[str] = None
        self._cancel = False
        self._pause_now = False
        self._run_started = 0.0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> RunState:
        if not self.store.verify(self.run_id):
            raise TamperedLogError(f"The event log for run {self.run_id} does not verify.")
        state = self.state_refetch()
        # Recovery (spec 5.6): orphaned attempts are aborted, tasks return to ready.
        for ts in state.tasks.values():
            for a in ts.attempts:
                if a.status == "running":
                    self.store.append(self.run_id, "attempt.aborted", {
                        "task_id": ts.task_id, "attempt_id": a.attempt_id,
                        "reason": "recovered-after-restart"})
        self.state = self.state_refetch()
        self._run_started = self.config.clock()
        return self.state

    def state_refetch(self) -> RunState:
        events = self.store.events(self.run_id)
        plan_obj = events[0].payload.get("plan")
        self.state = replay(self.store, self.run_id, Plan.from_obj(plan_obj))
        return self.state

    # -- public API ----------------------------------------------------------

    def run(self, pause_after: Optional[str] = None) -> list[StepResult]:
        """Step until the run is done, waiting at a gate, paused or cancelled."""
        results: list[StepResult] = []
        self._pause_after = pause_after
        if self.state.status == "queued":
            self.store.append(self.run_id, "run.resumed", {"reason": "started"})
            self.state_refetch()
        while True:
            result = self.step()
            results.append(result)
            if result.kind in ("run_succeeded", "run_failed", "gate_waiting",
                               "paused", "cancelled"):
                break
            if result.kind == "task_failed":
                break                      # policy=ask paused the run; policy handled inside
        return results

    def step(self) -> StepResult:
        st = self.state
        if self._cancel:
            self.store.append(self.run_id, "run.cancelled", {"reason": "user"})
            self.state_refetch()
            return StepResult("cancelled")
        if st.status in ("succeeded", "failed", "cancelled"):
            return StepResult(st.status if st.status != "succeeded" else "run_succeeded",
                              code=st.failure_code)
        if st.status == "paused":
            return StepResult("paused", detail=st.paused_reason)
        tid = st.next_task()
        if tid is None:
            totals = self._totals()
            self.store.append(self.run_id, "run.succeeded", totals)
            self.state_refetch()
            return StepResult("run_succeeded", usage=totals)
        task = st.plan.task(tid)
        ts = st.task_state(tid)
        if ts.state == "awaiting_approval":
            return StepResult("gate_waiting", tid, detail=ts.gate_reason)

        # Gate check (5.6 step 2): explicit gate, or the Rule of Two defence.
        # An approved gate is never opened twice.
        flags = self._flags_map().get(tid, (False, False, False))
        if tid not in st.approved_gates:
            if task.gate_before:
                return self._open_gate(task, "user")
            if all(flags) and not task.gate_before:
                return self._open_gate(task, "rule_of_two")

        return self._execute_task(task)

    # -- controls (RUN-6) ------------------------------------------------------

    def request_pause_now(self) -> None:
        self._pause_now = True

    def request_cancel(self) -> None:
        self._cancel = True

    def approve_gate(self, task_id: str, reason: str = "") -> None:
        ts = self.state.task_state(task_id)
        if ts is None or ts.state != "awaiting_approval":
            raise ValueError(f"task {task_id} is not waiting at a gate")
        self.store.append(self.run_id, "gate.approved", {
            "task_id": task_id, "shown_hash": ts.gate_shown_hash,
            "decision": "approve", "reason": reason})
        if self.state.status == "paused":
            self.store.append(self.run_id, "run.resumed",
                              {"reason": f"gate approved: {task_id}"})
        self.state_refetch()

    def reject_gate(self, task_id: str, decision: str = "stop", reason: str = "") -> None:
        if decision not in ("stop", "skip", "edit"):
            raise ValueError("decision must be stop, skip or edit")
        ts = self.state.task_state(task_id)
        if ts is None or ts.state != "awaiting_approval":
            raise ValueError(f"task {task_id} is not waiting at a gate")
        self.store.append(self.run_id, "gate.rejected", {
            "task_id": task_id, "shown_hash": ts.gate_shown_hash,
            "decision": decision, "reason": reason})
        if decision == "stop":
            self.store.append(self.run_id, "run.cancelled", {"reason": f"gate rejected: {task_id}"})
        elif self.state.status == "paused":
            self.store.append(self.run_id, "run.resumed",
                              {"reason": f"gate rejected ({decision}): {task_id}"})
        self.state_refetch()

    def retry(self, task_id: str) -> None:
        ts = self.state.task_state(task_id)
        if ts is None or ts.state != "failed":
            raise ValueError(f"task {task_id} is not failed")
        self.store.append(self.run_id, "task.ready", {"task_id": task_id})
        if self.state.status in ("paused", "failed"):
            self.store.append(self.run_id, "run.resumed", {"reason": f"retry {task_id}"})
        self.state_refetch()

    def skip(self, task_id: str, cascade: bool = True) -> list[str]:
        """Skip a task; dependent tasks are marked skipped too (spec 5.7)."""
        skipped = [task_id]
        self.store.append(self.run_id, "task.skipped", {"task_id": task_id, "code": "user-skip"})
        if cascade:
            for t in self.state.plan.tasks:
                if t.id == task_id:
                    continue
                if any(dep in skipped for dep in t.needs):
                    if self.state.task_state(t.id).state not in ("succeeded", "skipped"):
                        self.store.append(self.run_id, "task.skipped", {
                            "task_id": t.id, "code": "blocked-by-skip"})
                        skipped.append(t.id)
        if self.state.status in ("paused", "failed"):
            self.store.append(self.run_id, "run.resumed", {"reason": f"skip {task_id}"})
        self.state_refetch()
        return skipped

    def rerun_from(self, task_id: str) -> list[str]:
        """Rerun from here (spec 5.10): N..last go stale, then pending."""
        plan = self.state.plan
        idx = plan.order_index(task_id)
        if idx < 0:
            raise ValueError(f"unknown task {task_id}")
        stale: list[str] = []
        for t in plan.tasks[idx:]:
            if self.state.task_state(t.id).state in ("succeeded", "failed", "skipped",
                                                     "stale", "ready", "pending"):
                self.store.append(self.run_id, "task.stale", {"task_id": t.id})
                self.store.append(self.run_id, "task.ready", {"task_id": t.id})
                stale.append(t.id)
        if self.state.status in ("paused", "failed", "succeeded"):
            self.store.append(self.run_id, "run.resumed",
                              {"reason": f"rerun from {task_id}"})
        self.state_refetch()
        return stale

    # -- internals: gates -----------------------------------------------------

    def _open_gate(self, task: Task, reason: str) -> StepResult:
        ctx = self._assemble(task)
        shown_hash = hashlib.sha256(canonical_json(ctx.request_obj()).encode()).hexdigest()
        self.store.append(self.run_id, "gate.waiting", {
            "task_id": task.id, "reason": reason, "shown_hash": shown_hash})
        # A run waiting at a gate is paused until the user decides (S7).
        if self.state.status != "paused":
            self.store.append(self.run_id, "run.paused",
                              {"reason": f"gate waiting: {task.id}"})
        self.state_refetch()
        return StepResult("gate_waiting", task.id, detail=reason)

    # -- internals: task execution ----------------------------------------------

    def _execute_task(self, task: Task) -> StepResult:
        st = self.state
        limits = task.limits or {}
        retries = int(limits.get("retries", DEFAULTS["retries"]))
        backoff_index = 0
        stall_retried = False
        loop_retried = False
        params_temperature = float((task.params or {}).get("temperature", 0.3))
        usage_total = {"tokens_in": 0, "tokens_out": 0, "duration_ms": 0, "attempts": 0}
        task_start = self.config.clock()

        attempt_no = len(st.task_state(task.id).attempts) if st.task_state(task.id) else 0
        while True:
            if self._cancel:
                self.store.append(self.run_id, "run.cancelled", {"reason": "user"})
                self.state_refetch()
                return StepResult("cancelled")
            # Run-level caps (RUN-8): checked before each attempt and after each call.
            run_tokens = st.tokens_used()
            if run_tokens >= self.config.cap_tokens_run:
                return self._cap_stop("tokens", task)
            if (self.config.clock() - self._run_started) > self.config.cap_wall_minutes_run * 60:
                return self._cap_stop("wall-time", task)

            attempt_no += 1
            outcome = self._run_attempt(task, attempt_no, params_temperature)
            usage_total["tokens_in"] += outcome.tokens_in
            usage_total["tokens_out"] += outcome.tokens_out
            usage_total["attempts"] += 1

            if outcome.kind == "control":
                if outcome.code == "paused":
                    return StepResult("paused", task.id, detail="pause requested")
                return StepResult("cancelled", task.id)

            if outcome.ok:
                usage_total["duration_ms"] = int(
                    (self.config.clock() - task_start) * 1000)
                self._record_success(task, outcome, usage_total)
                if self._pause_after == task.id:
                    self.store.append(self.run_id, "run.paused",
                                      {"reason": f"pause after {task.id}"})
                    self.state_refetch()
                    return StepResult("task_succeeded", task.id,
                                      detail="run paused after this task")
                return StepResult("task_succeeded", task.id, usage=usage_total)

            if outcome.code == "CAP-REACHED":
                return self._cap_stop("tokens", task)

            # Automatic retries (spec 5.7 table).
            if outcome.kind == "provider_error" and backoff_index < retries:
                delay = BACKOFF_SCHEDULE[min(backoff_index, len(BACKOFF_SCHEDULE) - 1)]
                backoff_index += 1
                self.config.sleeper(delay)
                continue
            if outcome.kind == "stall" and not stall_retried:
                stall_retried = True
                continue
            if outcome.kind == "loop" and not loop_retried:
                loop_retried = True
                params_temperature = round(params_temperature + 0.2, 2)
                continue
            break

        self.store.append(self.run_id, "task.failed", {
            "task_id": task.id, "code": outcome.code, "usage": usage_total})
        self.state_refetch()
        return self._apply_failure_policy(task, outcome.code, usage_total)

    def _cap_stop(self, cap: str, task: Task | None = None) -> StepResult:
        if task is not None:
            self.store.append(self.run_id, "task.failed", {
                "task_id": task.id, "code": "CAP-REACHED",
                "usage": {"cap": cap}})
        self.store.append(self.run_id, "run.failed", {
            "code": "CAP-REACHED", "cap": cap,
            "totals": self._totals()})
        self.state_refetch()
        return StepResult("run_failed", task_id=task.id if task else "",
                          code="CAP-REACHED", detail=cap)

    def _apply_failure_policy(self, task: Task, code: str, usage: dict) -> StepResult:
        policy = self.config.failure_policy
        if policy == "skip":
            self.skip(task.id, cascade=True)
            return StepResult("task_skipped", task.id, code=code, usage=usage)
        if policy == "stop":
            self.store.append(self.run_id, "run.failed", {
                "code": code, "task_id": task.id, "totals": self._totals()})
            self.state_refetch()
            return StepResult("run_failed", task_id=task.id, code=code, usage=usage)
        # ask: pause the run and surface the failure panel (S8).
        self.store.append(self.run_id, "run.paused", {
            "reason": f"task {task.id} failed: {code}"})
        self.state_refetch()
        return StepResult("task_failed", task.id, code=code, usage=usage)

    def _record_success(self, task: Task, outcome: AttemptOutcome, usage: dict) -> None:
        handoff = outcome.handoff
        # The engine fills missing deliverable hashes (H-07 verifies the rest).
        workspace = self._workspace()
        for d in handoff.deliverables:
            if d.path and not d.sha256 and workspace.exists(d.path):
                d.sha256 = workspace.sha256(d.path)
        handoff.usage = usage
        handoff.raw_output_ref = f"attempts/{task.id}/final.txt"
        self.store.write_handoff(self.run_id, task.id, handoff.to_obj())
        self.store.append(self.run_id, "handoff.recorded", {
            "task_id": task.id, "attempt_id": outcome.attempt_id,
            "handoff": handoff.to_obj(), "validation": "ok"})
        # Constraint releases: an explicit record with authority and evidence (HIL-3).
        for hc in handoff.binding_constraints:
            if hc.state != "resolved":
                continue
            plan_c = self.state.plan.constraint(hc.id)
            if plan_c is None or not self.state.constraint_active(hc.id):
                continue
            ev = hc.resolution_evidence or {}
            self.store.append(self.run_id, "constraint.released", {
                "constraint_id": hc.id, "authority": hc.authority,
                "evidence": ev.get("ref", ""), "task_id": task.id})
        self.store.append(self.run_id, "task.succeeded", {
            "task_id": task.id, "code": "ok", "usage": usage})
        self.state_refetch()

    # -- internals: one attempt ---------------------------------------------------

    def _run_attempt(self, task: Task, attempt_no: int, temperature: float) -> AttemptOutcome:
        st = self.state
        limits = task.limits or {}
        max_turns = int(limits.get("max_turns", DEFAULTS["max_turns"]))
        max_tool_calls = int(limits.get("max_tool_calls", DEFAULTS["max_tool_calls"]))
        timeout = float(limits.get("timeout_seconds", DEFAULTS["wall_task_seconds"]))
        max_output = int((task.params or {}).get("max_tokens", DEFAULTS["max_output_tokens"]))
        attempt_id = f"att{attempt_no:03d}-{secrets.token_hex(3)}"

        # Context assembly with the budget ladder (spec 5.4).  Overflow never
        # starts the attempt (RUN-9).
        ctx = self._assemble(task)
        if ctx.overflow:
            return AttemptOutcome(ok=False, code="RUN-CONTEXT-OVERFLOW", kind="terminal")
        for step in ctx.compaction_steps:
            self.store.append(self.run_id, "compaction.applied", {
                "task_id": task.id, "ladder_step": step,
                "tokens_before": ctx.token_estimate, "tokens_after": ctx.token_estimate})

        provider_id, model_id, adapter = self._resolve(task)
        params = dict(task.params or {})
        params.setdefault("temperature", temperature)
        params.setdefault("max_tokens", max_output)

        messages = list(ctx.messages)
        broker = self._broker()
        native_tools = self.config.tool_calling
        tools = broker.tool_defs(task) if native_tools else []
        if not native_tools:
            messages[0] = Message("system", messages[0].content + "\n\n" + TEXT_TOOL_PROTOCOL)

        ts_state = st.task_state(task.id)
        if ts_state is None or ts_state.state not in ("ready", "running"):
            self.store.append(self.run_id, "task.ready", {"task_id": task.id})
        request_hash = hashlib.sha256(canonical_json(
            [m.to_obj() for m in messages] + [t.name for t in tools]
            + [params, model_id]).encode()).hexdigest()
        self.store.append(self.run_id, "attempt.started", {
            "task_id": task.id, "attempt_id": attempt_id, "provider": provider_id,
            "model": model_id, "params": params, "request_hash": request_hash})
        self.state_refetch()
        self.store.write_attempt_file(self.run_id, task.id, attempt_id, "request.json",
                                      json.dumps({"model": model_id, "params": params,
                                                  "messages": [m.to_obj() for m in messages],
                                                  "tools": [t.name for t in tools]},
                                                 ensure_ascii=False, indent=1))

        attempt_start = self.config.clock()
        messages_path = "messages.jsonl"
        tokens_out_total = 0
        tokens_in_total = ctx.token_estimate
        tool_calls_made = 0
        denial_count = 0
        consecutive_tool: list[tuple[str, str]] = []
        assistant_texts: list[str] = []
        tool_names_called: set[str] = set()
        repaired_tool_call = False
        repaired_handoff = False

        def log_message(message: Message, extra_tokens: int = 0):
            entry = {"ts": _now_iso(), "role": message.role, "content": message.content,
                     "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                    for c in message.tool_calls],
                     "tool_call_id": message.tool_call_id, "untrusted": message.untrusted}
            self.store.write_attempt_file(self.run_id, task.id, attempt_id, messages_path,
                                          json.dumps(entry, ensure_ascii=False) + "\n",
                                          append=True)
            self.store.append(self.run_id, "message.added", {
                "task_id": task.id, "attempt_id": attempt_id, "role": message.role,
                "ref": f"attempts/{task.id}/{attempt_id}/{messages_path}",
                "tokens": extra_tokens})

        turn = 0
        while True:
            turn += 1
            if turn > max_turns:
                return AttemptOutcome(False, "CAP-REACHED", "terminal",
                                      attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)
            if self.config.clock() - attempt_start > timeout:
                return AttemptOutcome(False, "TIMEOUT", "stall", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)
            if st.tokens_used() + tokens_in_total + tokens_out_total >= self.config.cap_tokens_run:
                return AttemptOutcome(False, "CAP-REACHED", "terminal", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)

            request = ChatRequest(model=model_id, messages=messages, tools=tools,
                                  params=dict(params))
            try:
                text, reasoning, tool_calls, usage = self._stream(
                    adapter, request, task, attempt_id)
            except PauseRequested:
                self._abort_attempt(task, attempt_id)
                self.store.append(self.run_id, "run.paused", {"reason": "pause requested"})
                self.state_refetch()
                return AttemptOutcome(False, "paused", "control", attempt_id=attempt_id)
            except CancelRequested:
                self._abort_attempt(task, attempt_id)
                self.store.append(self.run_id, "run.cancelled", {"reason": "user"})
                self.state_refetch()
                return AttemptOutcome(False, "cancelled", "control", attempt_id=attempt_id)
            except SimulatedCrash:
                raise                      # fault injection must propagate (AC-06)
            except StallError:
                return AttemptOutcome(False, "STALL", "stall", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)
            except ProviderError as exc:
                return AttemptOutcome(False, exc.code, "provider_error", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)

            if usage is not None:
                tokens_in_total = max(tokens_in_total, usage.tokens_in)
                tokens_out_total += usage.tokens_out
            else:
                tokens_out_total += estimate_tokens(text)

            # Run-level token cap: at most one model call beyond the cap (AC-07).
            if st.tokens_used() + tokens_in_total + tokens_out_total > self.config.cap_tokens_run:
                return AttemptOutcome(False, "CAP-REACHED", "terminal", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)

            # Text tool protocol (TLS-3): calls arrive inside the reply.
            if not native_tools and not tool_calls:
                parsed = parse_text_tool_call(text)
                if parsed is not None:
                    tool_calls = [ToolCall(f"call_{turn}", parsed[0],
                                           parsed[1] if isinstance(parsed[1], str)
                                           else json.dumps(parsed[1]))]
                elif "<tool_call>" in text:
                    if repaired_tool_call:
                        return AttemptOutcome(False, "TOOL-CALL-INVALID", "terminal",
                                              attempt_id=attempt_id,
                                              tokens_in=tokens_in_total,
                                              tokens_out=tokens_out_total)
                    repaired_tool_call = True
                    messages.append(Message("assistant", text))
                    log_message(Message("assistant", text))
                    messages.append(Message(
                        "user", "Your <tool_call> block could not be read. Reply again with "
                                "exactly one valid <tool_call>{\"name\": ..., \"arguments\": "
                                "{...}}</tool_call> line."))
                    continue

            assistant = Message("assistant", text, tool_calls=list(tool_calls))
            messages.append(assistant)
            log_message(assistant, estimate_tokens(text))
            self.store.write_attempt_file(self.run_id, task.id, attempt_id, "final.txt.turn",
                                          text)
            assistant_texts.append(text.strip())

            # Loop detection (spec 5.7): four identical non-empty assistant
            # messages.  Tool-call-only turns have empty text and are normal.
            if (len(assistant_texts) >= 4 and assistant_texts[-1]
                    and len(set(assistant_texts[-4:])) == 1):
                return AttemptOutcome(False, "LOOP-DETECTED", "loop", attempt_id=attempt_id,
                                      tokens_in=tokens_in_total, tokens_out=tokens_out_total)

            if tool_calls:
                for tc in tool_calls:
                    tool_calls_made += 1
                    if tool_calls_made > max_tool_calls:
                        return AttemptOutcome(False, "CAP-REACHED", "terminal",
                                              attempt_id=attempt_id,
                                              tokens_in=tokens_in_total,
                                              tokens_out=tokens_out_total)
                    args, parse_error = _parse_tool_args(tc.arguments)
                    if parse_error:
                        if repaired_tool_call:
                            return AttemptOutcome(False, "TOOL-CALL-INVALID", "terminal",
                                                  attempt_id=attempt_id,
                                                  tokens_in=tokens_in_total,
                                                  tokens_out=tokens_out_total)
                        repaired_tool_call = True
                        messages.append(Message(
                            "user", f"The arguments of tool {tc.name} could not be read as a "
                                    "JSON object. Call it again with valid JSON arguments."))
                        break
                    # Loop detection: the same tool call three times in a row.
                    consecutive_tool.append((tc.name, tc.arguments))
                    if (len(consecutive_tool) >= 3
                            and len(set(consecutive_tool[-3:])) == 1):
                        return AttemptOutcome(False, "LOOP-DETECTED", "loop",
                                              attempt_id=attempt_id,
                                              tokens_in=tokens_in_total,
                                              tokens_out=tokens_out_total)
                    decision = broker.execute(tc.name, args, task)
                    if decision.allowed:
                        tool_names_called.add(tc.name)
                        self.store.append(self.run_id, "tool.called", {
                            "task_id": task.id, "attempt_id": attempt_id, "tool": tc.name,
                            "arguments_hash": hashlib.sha256(
                                canonical_json(args).encode()).hexdigest(),
                            "result": decision.result[:2000], "duration_ms": 0})
                        result_text = decision.result
                    else:
                        denial_count += 1
                        self.store.append(self.run_id, "tool.denied", {
                            "task_id": task.id, "attempt_id": attempt_id, "tool": tc.name,
                            "rule": decision.rule})
                        if denial_count >= 2:
                            return AttemptOutcome(False, "PERMISSION-DENIED", "terminal",
                                                  attempt_id=attempt_id,
                                                  tokens_in=tokens_in_total,
                                                  tokens_out=tokens_out_total)
                        result_text = (f"DENIED ({decision.rule}): the tool {tc.name} is not "
                                       "available for this task. Do not call it again.")
                    result_msg = Message("tool", result_text, tool_call_id=tc.id,
                                         name=tc.name, untrusted=True)
                    messages.append(result_msg)
                    log_message(result_msg)
                else:
                    continue          # all tool calls handled -> next turn
                continue              # after an argument repair -> next turn

            # Final message: validate the handoff (spec 5.5).
            self.store.write_attempt_file(self.run_id, task.id, attempt_id, "final.txt", text)
            handoff = extract_handoff(text)
            evidence = EvidenceContext(
                attempt_tool_calls=tool_names_called,
                succeeded_tasks={tid for tid, t in st.tasks.items() if t.state == "succeeded"},
                user_approvals=set(st.approved_gates))
            issues = validate_handoff(handoff, task, st.plan, self._workspace(), evidence)
            if issues:
                if repaired_handoff:
                    return AttemptOutcome(False, "HANDOFF-INVALID", "terminal",
                                          attempt_id=attempt_id, final_text=text,
                                          tokens_in=tokens_in_total,
                                          tokens_out=tokens_out_total)
                repaired_handoff = True
                rules = ", ".join(sorted({i.rule for i in issues}))
                messages.append(Message(
                    "user",
                    "Your handoff failed validation (" + rules + "): "
                    + " ".join(i.message for i in issues)
                    + " Reply with the corrected final message ending with the <handoff> "
                      "block. Copy binding constraints verbatim."))
                log_message(messages[-1])
                continue
            return AttemptOutcome(True, "", "ok", attempt_id=attempt_id, handoff=handoff,
                                  final_text=text, tokens_in=tokens_in_total,
                                  tokens_out=tokens_out_total)

    def _abort_attempt(self, task: Task, attempt_id: str) -> None:
        self.store.append(self.run_id, "attempt.aborted", {
            "task_id": task.id, "attempt_id": attempt_id, "reason": "user"})
        self.store.append(self.run_id, "task.ready", {"task_id": task.id})
        self.state_refetch()

    # -- internals: streaming ---------------------------------------------------

    def _stream(self, adapter: Adapter, request: ChatRequest, task: Task, attempt_id: str):
        text = ""
        reasoning = ""
        tool_calls: list[ToolCall] = []
        usage = None
        last_event = self.config.clock()
        stream_log: list[str] = []
        try:
            for ev in adapter.chat(request):
                if self._cancel:
                    raise CancelRequested()
                if self._pause_now:
                    raise PauseRequested()
                now = self.config.clock()
                if now - last_event > self.config.stall_timeout:
                    raise StallError(f"no tokens for {self.config.stall_timeout}s")
                last_event = now
                stream_log.append(json.dumps(
                    {"kind": ev.kind, "text": ev.text,
                     "tool": ev.tool_call.name if ev.tool_call else None},
                    ensure_ascii=False))
                if ev.kind == "text_delta":
                    text += ev.text
                elif ev.kind == "reasoning_delta":
                    reasoning += ev.text
                elif ev.kind == "tool_call" and ev.tool_call is not None:
                    tool_calls.append(ev.tool_call)
                elif ev.kind == "usage" and ev.usage is not None:
                    usage = ev.usage
        finally:
            if stream_log:
                self.store.write_attempt_file(
                    self.run_id, task.id, attempt_id, "stream.log",
                    "\n".join(stream_log) + "\n")
        if reasoning:
            self.store.write_attempt_file(self.run_id, task.id, attempt_id, "reasoning.txt",
                                          reasoning)
        return text, reasoning, tool_calls, usage

    # -- internals: helpers --------------------------------------------------------

    def _assemble(self, task: Task) -> AssembledContext:
        st = self.state
        handoffs: dict[str, Handoff] = {}
        for dep in task.needs:
            ts = st.tasks.get(dep)
            if ts is not None and ts.state == "succeeded" and ts.handoff:
                handoffs[dep] = Handoff.from_obj(ts.handoff)
        provider_id, model_id, adapter = self._resolve(task)

        def summarizer(summary: str, max_words: int) -> str:
            try:
                words = summary.split()
                if len(words) <= max_words:
                    return summary
                req = ChatRequest(
                    model=model_id,
                    messages=[Message("system", "Shorten the text to at most the stated "
                                                    "number of words. Reply with the shortened "
                                                    "text only."),
                              Message("user", f"At most {max_words} words:\n{summary}")],
                    params={"max_tokens": max_words * 3})
                text = ""
                for ev in adapter.chat(req):
                    if ev.kind == "text_delta":
                        text += ev.text
                return " ".join(text.split()[:max_words])
            except ProviderError:
                return " ".join(summary.split()[:max_words])

        def token_counter(messages):
            try:
                return adapter.count_tokens(messages, model_id)
            except (ProviderError, AttributeError, NotImplementedError):
                return None

        ctx = assemble_context(
            task, st.plan, handoffs, self._workspace(), self._broker(),
            context_length=int((task.params or {}).get(
                "context_length", self.config.context_length)),
            summarizer=summarizer, token_counter=None)
        return ctx

    def _broker(self) -> PermissionBroker:
        workspace = self._workspace()
        return PermissionBroker(
            workspace, enable_shell=self.config.enable_shell,
            enable_web=self.config.enable_web,
            command_allowlist=self.config.command_allowlist,
            web_domains=self.config.web_domains)

    def _workspace(self):
        from las.modules.security import Workspace
        return Workspace(self.store.workspace_dir(self.run_id))

    def _resolve(self, task: Task) -> tuple[str, str, Adapter]:
        if task.model and task.model.get("provider_id"):
            pid = task.model["provider_id"]
            mid = task.model.get("model_id", "")
        else:
            role = task.role if task.role in self.models else "default"
            pid, mid = self.models.get(role, self.models.get("default", ("", "")))
        return pid, mid, self.registry.get(pid)

    def _flags_map(self) -> dict[str, tuple[bool, bool, bool]]:
        broker = self._broker()
        flags: dict[str, tuple[bool, bool, bool]] = {}
        for t in self.state.plan.tasks:
            reviewed: set[str] = set()
            if t.gate_before and t.id in self.state.approved_gates:
                reviewed = set(t.needs)
            flags[t.id] = broker.compute_flags(t, self.state.plan, flags, reviewed)
        return flags

    def _totals(self) -> dict:
        st = self.state
        return {
            "tasks_total": len(st.plan.tasks),
            "tasks_succeeded": sum(1 for t in st.tasks.values() if t.state == "succeeded"),
            "tasks_skipped": sum(1 for t in st.tasks.values() if t.state == "skipped"),
            "tokens": st.tokens_used(),
        }


def _parse_tool_args(raw: str) -> tuple[dict, bool]:
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(obj, dict):
            return obj, False
    except (json.JSONDecodeError, ValueError):
        pass
    return {}, True


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
