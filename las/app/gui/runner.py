"""Background execution for the GUI.

Two thread types:

- :class:`PlanningJob` runs the planner off the UI thread and can be
  cancelled between rounds.
- :class:`RunSession` drives a run step-by-step.  When the run stops at
  an approval gate, a failure with the Ask policy, or a user pause, the
  worker posts a ``waiting`` event and blocks until the UI posts a
  decision - so the event log stays the single source of truth while
  the user keeps control.
"""

from __future__ import annotations

import dataclasses
import queue
import threading

from las.modules.execution import DriverConfig, Engine
from las.modules.planning import CancelledPlanning, Planner
from las.modules.storage import TamperedLogError


class PlanningJob(threading.Thread):
    """Planner in a thread; ``events`` carries one ('done', PlanResult)."""

    def __init__(self, adapter, model: str, goal: str, options: dict,
                 params: dict | None = None, json_schema_supported: bool = True):
        super().__init__(daemon=True)
        self.adapter = adapter
        self.model = model
        self.goal = goal
        self.options = options
        self.params = params or {}
        self.json_schema_supported = json_schema_supported
        self.events: queue.Queue = queue.Queue()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _check(self):
        if self._cancelled.is_set():
            raise CancelledPlanning()

    def _on_reply(self, text: str) -> None:
        self.events.put(("reply", text))

    def run(self) -> None:
        planner = Planner(self.adapter, self.model, params=self.params,
                          json_schema_supported=self.json_schema_supported,
                          on_reply=self._on_reply)
        try:
            result = planner.plan(self.goal, self.options,
                                  cancel_check=self._check)
            self.events.put(("done", result))
        except Exception as exc:                      # noqa: BLE001
            self.events.put(("error", str(exc)))


class RunSession(threading.Thread):
    """Interactive run driver.

    The worker loop calls ``driver.run(pause_after=...)`` and inspects
    the last StepResult:

    - terminal kinds end the session;
    - ``gate_waiting`` posts the gate and waits for a decision;
    - a failure under the Ask policy posts the failure and waits;
    - a user pause waits for Resume / Pause-after / Cancel;
    - ``Pause now`` sets the driver flag; the current attempt aborts and
      restarts on resume (spec S6).

    Decisions are posted through :meth:`decide`; the worker applies them
    to the same driver (or a rebuilt one for a model change).
    """

    def __init__(self, engine: Engine, run_id: str, registry, models: dict,
                 config: DriverConfig):
        super().__init__(daemon=True)
        self.engine = engine
        self.run_id = run_id
        self.registry = registry
        self.models = dict(models)
        self.config = config
        self.events: queue.Queue = queue.Queue()
        self._decision: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._pause_after: str | None = None
        self._pause_requested = False
        self._driver = None
        self.finished = threading.Event()

    # -- UI side -------------------------------------------------------------

    def pause_after(self, task_id: str) -> None:
        self._pause_after = task_id
        self._decision.put(("resume", {}))

    def request_pause_now(self) -> None:
        self._pause_requested = True
        if self._driver is not None:
            self._driver.request_pause_now()

    def request_cancel(self) -> None:
        """Mid-attempt cancel: the driver flag aborts the current model
        call.  Decisions posted while waiting go through decide()."""
        if self._driver is not None:
            self._driver.request_cancel()

    def decide(self, action: str, **kw) -> None:
        self._decision.put((action, kw))

    # -- worker side ---------------------------------------------------------

    def _post(self, kind: str, **payload) -> None:
        self.events.put((kind, payload))

    def _await_decision(self, valid: set) -> tuple[str, dict]:
        """Block for a decision, discarding stale actions that belonged
        to an earlier waiting state."""
        while True:
            action, kw = self._decision.get()
            if action in valid:
                return action, kw

    def run(self) -> None:                            # noqa: C901
        try:
            self._driver = self.engine.driver(
                self.run_id, self.registry, self.models, self.config)
            self._driver.open()
            self._post("started", run_id=self.run_id)
            while not self._stop.is_set():
                pause_after = self._pause_after
                self._pause_after = None
                results = self._driver.run(pause_after=pause_after)
                self._post("results",
                           results=[dataclasses.asdict(r) for r in results])
                last = results[-1] if results else None
                if last is None:
                    break
                kind = last.kind
                if kind in ("run_succeeded", "run_failed", "cancelled"):
                    self._post("finished", kind=kind, code=last.code)
                    break
                if kind == "gate_waiting":
                    self._post("waiting", reason="gate", task_id=last.task_id,
                               detail=last.detail)
                    action, kw = self._await_decision(
                        {"approve", "reject", "cancel"})
                    if not self._apply_gate(action, kw, last.task_id):
                        break
                    continue
                if kind == "task_failed":
                    # Ask policy: stop the loop and let the user choose.
                    self._post("waiting", reason="failure", task_id=last.task_id,
                               code=last.code, detail=last.detail,
                               usage=last.usage)
                    action, kw = self._await_decision(
                        {"retry", "skip", "rerun", "stop"})
                    if not self._apply_failure(action, kw, last.task_id):
                        self._post("finished", kind="run_failed",
                                   code=last.code or "FAILED")
                        break
                    continue
                if kind == "paused":
                    self._post("waiting", reason="pause", task_id=last.task_id,
                               detail=last.detail)
                    action, kw = self._await_decision({"resume", "cancel"})
                    if action == "cancel":
                        self._driver.request_cancel()
                        self._driver.step()
                        self._post("finished", kind="cancelled", code="")
                        break
                    # "resume" (with optional new pause_after) continues.
                    continue
                # task_succeeded / task_skipped within a finished run
                if kind in ("task_succeeded", "task_skipped"):
                    continue
                self._post("finished", kind="run_failed", code=last.code)
                break
        except TamperedLogError:
            self._post("finished", kind="run_failed", code="TAMPERED")
        except Exception as exc:                      # noqa: BLE001
            self._post("finished", kind="run_failed", code="INTERNAL",
                       detail=str(exc))
        finally:
            self.finished.set()
            self._post("session_done", run_id=self.run_id)

    def _apply_gate(self, action: str, kw: dict, task_id: str) -> bool:
        if action == "approve":
            self._driver.approve_gate(task_id, reason=kw.get("reason", ""))
            return True
        if action == "reject":
            self._driver.reject_gate(
                task_id, decision=kw.get("decision", "stop"),
                reason=kw.get("reason", ""))
            return True
        # cancel
        self._driver.request_cancel()
        self._driver.step()
        self._post("finished", kind="cancelled", code="")
        return False

    def _apply_failure(self, action: str, kw: dict, task_id: str) -> bool:
        if action == "retry":
            self._driver.retry(task_id)
            if kw.get("model"):
                self.models["default"] = (self.models["default"][0], kw["model"])
                self._driver = self.engine.driver(
                    self.run_id, self.registry, self.models, self.config)
                self._driver.open()
            return True
        if action == "skip":
            cascade = kw.get("cascade", True)
            blocked = self._driver.skip(task_id, cascade=cascade)
            if blocked:
                self._post("info", blocked=blocked)
            return True
        if action == "rerun":
            self._driver.rerun_from(task_id)
            return True
        return False                                # stop run


