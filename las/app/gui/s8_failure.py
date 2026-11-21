"""S8 Failure panel (RUN-7): what to do when a task fails.

Reason code and plain explanation, attempts used, the last error, an
excerpt of the last assistant message and suggested fixes.  Actions:
Retry (instructions can be edited first), Retry with another model,
Skip (stop or continue with dependents marked Blocked by skip), Edit
task and rerun from here, and Stop run.  The default policy is Ask.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import Dialog
from las.shared.messages import message_with_fix


class FailureDialog(Dialog):
    def __init__(self, parent, app, run_id: str, payload: dict, session):
        self.app = app
        self.run_id = run_id
        self.session = session
        self.task_id = payload.get("task_id", "")
        code = payload.get("code", "FAILED")
        state = app.engine.state(run_id)
        ts = state.tasks.get(self.task_id)
        attempts = len(ts.attempts) if ts else 0

        super().__init__(parent, f"Task failed: {self.task_id}",
                         buttons=[("Retry", "Primary.TButton", "retry"),
                                  ("Stop run", "Danger.TButton", "stop")])
        msg, fix = message_with_fix(code)
        c = self._c

        ttk.Label(self.body, text=f"{code}", style="Heading.TLabel").pack(
            anchor="w")
        ttk.Label(self.body, text=msg, wraplength=460).pack(anchor="w",
                                                            pady=(4, 2))
        ttk.Label(self.body, text=f"Fix: {fix}", wraplength=460,
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(self.body, text=f"Attempts used: {attempts}").pack(
            anchor="w")

        # Last assistant excerpt
        excerpt = ""
        for ev in reversed(app.store.events(run_id)):
            p = ev.payload
            if p.get("task_id") == self.task_id and \
                    ev.type == "message.added" and p.get("role") == "assistant":
                excerpt = str(p.get("content", ""))[:600]
                break
        if excerpt:
            frame = ttk.Labelframe(self.body, text="Last assistant message")
            frame.pack(fill="x", pady=6)
            text = tk.Text(frame, wrap="word", height=4,
                           background=c["surface"], foreground=c["text"],
                           relief="solid", borderwidth=1, state="disabled",
                           font=("TkDefaultFont", 9))
            text.pack(fill="x", padx=4, pady=4)
            text.configure(state="normal")
            text.insert("1.0", excerpt)
            text.configure(state="disabled")

        # Editable instructions (Retry uses them)
        task = next((t for t in state.plan.tasks
                     if t.id == self.task_id), None)
        ed = ttk.Labelframe(self.body, text="Edit instructions before retry")
        ed.pack(fill="x", pady=6)
        self.instructions = tk.Text(ed, wrap="word", height=3,
                                    background=c["surface"],
                                    foreground=c["text"],
                                    insertbackground=c["text"],
                                    relief="solid", borderwidth=1,
                                    font=("TkDefaultFont", 9))
        self.instructions.pack(fill="x", padx=4, pady=4)
        self.instructions.insert("1.0", task.instructions if task else "")

        # More actions
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Retry with another model",
                   command=self._retry_other_model).pack(side="left")
        ttk.Button(row, text="Skip this task…",
                   command=self._skip).pack(side="left", padx=6)
        ttk.Button(row, text="Edit task and rerun from here",
                   command=self._rerun).pack(side="left")

    # -- actions ---------------------------------------------------------------

    def _edit_instructions(self) -> None:
        value = self.instructions.get("1.0", "end-1c").strip()
        if not value:
            return
        self.app.engine.edit_task(self.run_id, self.task_id,
                                  {"instructions": value},
                                  reason="edited before retry")

    def on_retry(self) -> None:
        self._edit_instructions()
        if self.session is not None:
            self.session.decide("retry")
        self.app.live.configure(text=f"Retrying {self.task_id}")

    def on_stop(self) -> None:
        if self.session is not None:
            self.session.decide("stop")
        self.app.live.configure(text="Run stopped")

    def _retry_other_model(self) -> None:
        models = []
        try:
            models = self.app.default_models()
        except Exception:                              # noqa: BLE001
            pass
        dlg = Dialog(self, "Retry with another model",
                     buttons=[("Retry", "Primary.TButton", "go"),
                              ("Cancel", "", "close")])
        var = tk.StringVar(value=models.get("default", ("", ""))[1])
        try:
            registry = self.app._provider_registry()
            ids = [p.get("id", p.id) if isinstance(p, dict) else p.id
                   for p in self.app.store.list_providers()]
            options = []
            for pid in ids:
                options += [m["id"] for m in registry.get(pid).list_models()]
        except Exception:                              # noqa: BLE001
            options = [var.get()]
        ttk.Label(dlg.body, text="Model").pack(anchor="w", padx=6, pady=4)
        ttk.Combobox(dlg.body, textvariable=var,
                     values=sorted(set(options))).pack(fill="x", padx=6)

        def go():
            self._edit_instructions()
            if self.session is not None:
                self.session.decide("retry", model=var.get())
            self.app.live.configure(
                text=f"Retrying {self.task_id} with {var.get()}")
            dlg.close()
        dlg.on_go = go
        dlg.wait_window()

    def _skip(self) -> None:
        state = self.app.engine.state(self.run_id)
        dependents = [t.id for t in state.plan.tasks
                      if self.task_id in t.needs]
        dlg = Dialog(self, "Skip this task",
                     buttons=[("Continue", "Primary.TButton", "cont"),
                              ("Stop the run", "Danger.TButton", "stop"),
                              ("Cancel", "", "close")])
        note = (f"{', '.join(dependents)} will be marked "
                "`Blocked by skip`." if dependents else
                "No other task depends on it.")
        ttk.Label(dlg.body, text=f"Skip {self.task_id}? {note}",
                  wraplength=380).pack(padx=8, pady=10)

        def cont():
            if self.session is not None:
                self.session.decide("skip", cascade=True)
            self.app.live.configure(text=f"Skipped {self.task_id}")
            dlg.close()

        def stop():
            if self.session is not None:
                self.session.decide("stop")
            dlg.close()

        dlg.on_cont = cont
        dlg.on_stop = stop
        dlg.wait_window()

    def _rerun(self) -> None:
        """Edit task and rerun from here (RUN-6): later tasks go stale."""
        self._edit_instructions()
        if self.session is not None:
            self.session.decide("rerun")
        self.app.live.configure(
            text=f"Rerunning from {self.task_id}; later tasks are stale")
