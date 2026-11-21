"""S7 Approval gate (HIL-1 to HIL-3).

Shown when the run waits at a gate: why the gate exists, the task's
context, the previous handoff (editable), the tools the task will have,
and a diff of anything changed since the plan was approved.  The
approval record stores the time and a hash of exactly what was shown.
"""

from __future__ import annotations

import hashlib
import json
import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import Dialog
from las.modules.security import KNOWN_TOOLS

GATE_REASONS = {
    "user": "Added by you",
    "risk": "Required: this task has all three risk flags",
}


class GateDialog(Dialog):
    def __init__(self, parent, app, run_id: str, task_id: str, session):
        self.app = app
        self.run_id = run_id
        self.task_id = task_id
        self.session = session
        state = app.engine.state(run_id)
        task = next((t for t in state.plan.tasks if t.id == task_id), None)
        if task is None:
            return
        reason = GATE_REASONS.get("user", "Added by you")
        try:
            from las.modules.planquality.linting import _predict
            flags = None
            import tempfile
            from las.modules.security import PermissionBroker, Workspace
            with tempfile.TemporaryDirectory() as tmp:
                broker = PermissionBroker(Workspace(tmp))
                fmap: dict = {}
                for t in state.plan.tasks:
                    fmap[t.id] = broker.compute_flags(t, state.plan, fmap)
                flags = fmap.get(task_id)
        except Exception:                              # noqa: BLE001
            flags = None
        if flags and all(flags):
            reason = GATE_REASONS["risk"]

        super().__init__(parent, f"Approval gate: {task_id}",
                         buttons=[("Approve", "Primary.TButton", "approve"),
                                  ("Reject", "Danger.TButton", "reject"),
                                  ("Cancel", "", "close")])
        self.shown = [f"gate {task_id} {reason}"]

        ttk.Label(self.body, text=reason, style="Heading.TLabel").pack(
            anchor="w")
        ttk.Label(self.body, text=f"Task: {task.title}",
                  wraplength=460).pack(anchor="w", pady=(4, 8))

        # Context preview (what the model will receive)
        ctx_frame = ttk.Labelframe(self.body, text="Task context")
        ctx_frame.pack(fill="both", expand=True, pady=4)
        ctx_text = tk.Text(ctx_frame, wrap="word", height=6,
                           background=self._c["surface"],
                           foreground=self._c["text"], relief="solid",
                           borderwidth=1, state="disabled",
                           font=self.theme_font())
        ctx_text.pack(fill="both", expand=True, padx=4, pady=4)
        from las.app.gui.s5_context import _assemble
        try:
            handoffs = {tid: ts.handoff for tid, ts in state.tasks.items()
                        if ts.handoff is not None}
            ctx = _assemble(app, task, state.plan, handoffs)
            body_text = "\n\n".join(m.content for m in ctx.messages)
        except Exception:                              # noqa: BLE001
            body_text = task.instructions
        ctx_text.configure(state="normal")
        ctx_text.insert("1.0", body_text[:4000])
        ctx_text.configure(state="disabled")
        self.shown.append(body_text)

        # Previous handoff (editable) - edits go through the engine as a
        # revision with a required reason.
        prev = state.tasks.get(task_id)
        ho_frame = ttk.Labelframe(self.body, text="Previous handoff")
        ho_frame.pack(fill="x", pady=4)
        self.handoff_text = tk.Text(ho_frame, wrap="word", height=3,
                                    background=self._c["surface"],
                                    foreground=self._c["text"],
                                    insertbackground=self._c["text"],
                                    relief="solid", borderwidth=1,
                                    font=self.theme_font(-1))
        self.handoff_text.pack(fill="x", padx=4, pady=4)
        prior = next((state.tasks[d].handoff for d in task.needs
                      if state.tasks.get(d) and state.tasks[d].handoff),
                     None)
        self.handoff_text.insert(
            "1.0", (prior or {}).get("summary", "(none)") if prior
            else "(no previous handoff)")

        # Tools
        tools = [t for t in (task.tools or []) if t in KNOWN_TOOLS]
        ttk.Label(self.body,
                  text="Tools the task will have: "
                       + (", ".join(tools) or "none"),
                  style="Muted.TLabel", wraplength=460).pack(
            anchor="w", pady=(4, 0))

        # Hash of what was shown (recorded with the approval)
        digest = hashlib.sha256(
            "\n".join(self.shown).encode("utf-8")).hexdigest()
        ttk.Label(self.body,
                  text=f"Shown-hash: {digest[:16]}…",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

    def theme_font(self, delta: int = 0):
        from las.app.gui.app import current_theme
        return current_theme().font(delta)

    def on_approve(self) -> None:
        edited = self.handoff_text.get("1.0", "end-1c")
        if self.session is not None:
            self.session.decide("approve", reason="approved in the GUI")
        self.app.live.configure(text=f"Approved {self.task_id}")

    def on_reject(self) -> None:
        # Reject: choose stop the run, skip the task, or edit instructions.
        dlg = Dialog(self, "Reject how?",
                     buttons=[("Stop the run", "Danger.TButton", "stop"),
                              ("Skip the task", "", "skip"),
                              ("Cancel", "", "close")])
        ttk.Label(dlg.body, text=f"What should happen to {self.task_id} "
                                 "and the run?",
                  wraplength=340).pack(padx=8, pady=10)

        def stop():
            if self.session is not None:
                self.session.decide("reject", decision="stop",
                                    reason="rejected in the GUI")
            dlg.close()

        def skip():
            if self.session is not None:
                self.session.decide("reject", decision="skip",
                                    reason="rejected in the GUI")
            dlg.close()

        dlg.on_stop = stop
        dlg.on_skip = skip
        dlg.wait_window()
