"""The run driver: lifecycle, task execution loop and controls
(spec 5.6, 5.10).

Private implementation file of the execution module; the facade re-exports
the public API. Exactly one session is active per run, in plan order
(RUN-2).  Every state change is written to the hash-chained event log
before the next action (RUN-5).
"""

from __future__ import annotations

from typing import Optional

from las.modules.context import AssembledContext, assemble_context
from las.shared.provider_contract import Adapter, ChatRequest, Message, ProviderError
from las.modules.execution import RunState, replay
from las.modules.execution import controls as _controls
from las.modules.execution import gates as _gates
from las.modules.execution.attempt import abort_attempt, run_attempt, stream
from las.modules.execution.budgets import (
    BACKOFF_SCHEDULE, DEFAULTS, AttemptOutcome, DriverConfig, StepResult,
)
from las.modules.handoffs import Handoff
from las.modules.providers import ProviderRegistry
from las.shared.plan_contract import Plan, Task
from las.modules.storage import Store, TamperedLogError, canonical_json
from las.modules.security import PermissionBroker, Workspace


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
        if self.state is not None:
            # Incremental (RUN-5): apply only events newer than the cursor.
            self.state = replay(self.store, self.run_id, self.state.plan,
                                state=self.state, after=self.state.seq)
            return self.state
        first = self.store.first_event(self.run_id)
        plan_obj = first.payload.get("plan")
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
        _controls.approve_gate(self.store, self.run_id, self.state, task_id, reason)
        self.state_refetch()

    def reject_gate(self, task_id: str, decision: str = "stop", reason: str = "") -> None:
        _controls.reject_gate(self.store, self.run_id, self.state, task_id,
                              decision, reason)
        self.state_refetch()

    def retry(self, task_id: str) -> None:
        _controls.retry(self.store, self.run_id, self.state, task_id)
        self.state_refetch()

    def skip(self, task_id: str, cascade: bool = True) -> list[str]:
        skipped = _controls.skip(self.store, self.run_id, self.state, task_id, cascade)
        self.state_refetch()
        return skipped

    def rerun_from(self, task_id: str) -> list[str]:
        stale = _controls.rerun_from(self.store, self.run_id, self.state, task_id)
        self.state_refetch()
        return stale

    def _open_gate(self, task: Task, reason: str) -> StepResult:
        result = _gates.open_gate(self.store, self.run_id, self.state.status,
                                  task, reason, self._assemble)
        self.state_refetch()
        return result

    def _flags_map(self) -> dict[str, tuple[bool, bool, bool]]:
        return _gates.flags_map(self._broker(), self.state.plan.tasks, self.state)

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
            outcome = run_attempt(self, task, attempt_no, params_temperature)
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
                used_in = used_out = 0
                for ev in adapter.chat(req):
                    if ev.kind == "text_delta":
                        text += ev.text
                    elif ev.kind == "usage" and ev.usage is not None:
                        used_in += int(getattr(ev.usage, "tokens_in", 0) or 0)
                        used_out += int(getattr(ev.usage, "tokens_out", 0) or 0)
                if used_in or used_out:
                    # Supporting calls count against real usage and
                    # budgets (mandate audit item 8).
                    self.store.append(self.run_id, "support.usage", {
                        "task_id": task.id, "kind": "summarizer",
                        "tokens_in": used_in, "tokens_out": used_out})
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
            summarizer=summarizer, token_counter=token_counter)
        return ctx

    def _broker(self) -> PermissionBroker:
        workspace = self._workspace()
        if self.config.broker_factory is not None:
            return self.config.broker_factory(workspace, self.config)
        return PermissionBroker(
            workspace, enable_shell=self.config.enable_shell,
            enable_web=self.config.enable_web,
            command_allowlist=self.config.command_allowlist,
            web_domains=self.config.web_domains)

    def _workspace(self):
        if self.config.workspace_factory is not None:
            return self.config.workspace_factory(self.store, self.run_id)
        return Workspace(self.store.workspace_dir(self.run_id))

    def _resolve(self, task: Task) -> tuple[str, str, Adapter]:
        if task.model and task.model.get("provider_id"):
            pid = task.model["provider_id"]
            mid = task.model.get("model_id", "")
        else:
            role = task.role if task.role in self.models else "default"
            pid, mid = self.models.get(role, self.models.get("default", ("", "")))
        return pid, mid, self.registry.get(pid)

    def _totals(self) -> dict:
        st = self.state
        return {
            "tasks_total": len(st.plan.tasks),
            "tasks_succeeded": sum(1 for t in st.tasks.values() if t.state == "succeeded"),
            "tasks_skipped": sum(1 for t in st.tasks.values() if t.state == "skipped"),
            "tokens": st.tokens_used(),
        }

