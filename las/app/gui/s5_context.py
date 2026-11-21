"""S5 Context preview (BLD-3, RUN-1): the exact input of one task.

Three tabs: As sent (the exact messages with section labels),
Structure (a table of token counts per block) and Warnings (predicted
overflow and compaction).  Counts are exact when the server has a
token-count endpoint and are labelled `estimated` otherwise.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import Dialog
from las.modules.context import assemble_context
from las.modules.security import PermissionBroker, Workspace
from las.shared.plan_contract import Plan, Task

SECTION_LABELS = {
    "system": "System block",
    "user": "Request",
    "assistant": "Assistant",
    "tool": "Tool result (UNTRUSTED)",
}


def _assemble(app, task: Task, plan: Plan, handoffs: dict | None):
    import tempfile
    handoffs = handoffs or {}
    with tempfile.TemporaryDirectory(prefix="las-ctx-") as tmp:
        broker = PermissionBroker(Workspace(tmp))
        ctx = assemble_context(
            task, plan, handoffs, Workspace(tmp), broker,
            context_length=int(
                app.store.get_setting("context_length") or 8192))
    return ctx


def preview_plan_task(parent, studio, task: Task) -> None:
    """Preview from the editor (no run yet: handoffs are empty)."""
    ctx = _assemble(studio.app, task, studio.plan, {})
    ContextPreviewDialog(parent, studio.app, ctx)


class ContextPreviewDialog(Dialog):
    def __init__(self, parent, app, run_id: str = "", task_id: str = "",
                 ctx=None):
        self.app = app
        self.ctx = ctx
        if ctx is None:
            state = app.engine.state(run_id)
            plan = state.plan
            task = next((t for t in plan.tasks if t.id == task_id), None)
            if task is None:
                return
            handoffs = {}
            for tid, ts in state.tasks.items():
                if ts.handoff is not None:
                    handoffs[tid] = ts.handoff
            self.ctx = _assemble(app, task, plan, handoffs)
        super().__init__(parent, "Context preview",
                         buttons=[("Copy", "", "copy"),
                                  ("Close", "", "close")])
        self.tabs = ttk.Notebook(self.body)
        self.tabs.pack(fill="both", expand=True)

        # As sent
        sent = ttk.Frame(self.tabs)
        self.tabs.add(sent, text="As sent")
        text = tk.Text(sent, wrap="word",
                       background=self._c["surface"],
                       foreground=self._c["text"],
                       font=("TkFixedFont", 9), relief="solid",
                       borderwidth=1)
        text.pack(fill="both", expand=True, padx=6, pady=6)
        for m in self.ctx.messages:
            label = SECTION_LABELS.get(m.role, m.role)
            text.insert("end", f"── {label} " + "─" * max(0, 40 - len(label))
                        + "\n")
            text.insert("end", m.content + "\n\n")
        text.configure(state="disabled")

        # Structure
        struct = ttk.Frame(self.tabs)
        self.tabs.add(struct, text="Structure")
        cols = ("block", "tokens")
        tree = ttk.Treeview(struct, columns=cols, show="headings", height=9)
        tree.heading("block", text="Block")
        tree.heading("tokens", text="Tokens")
        tree.column("block", width=280, anchor="w")
        tree.column("tokens", width=90, anchor="e")
        tree.pack(fill="x", padx=6, pady=6)
        for block, count in (self.ctx.breakdown or {}).items():
            tree.insert("", "end", values=(block, count))
        tree.insert("", "end", values=(
            "Total (estimated)" if not self.ctx.exact_counts
            else "Total (exact)", self.ctx.token_estimate))
        tree.insert("", "end", values=(
            "Available input", self.ctx.available_input))

        # Warnings
        warn = ttk.Frame(self.tabs)
        self.tabs.add(warn, text="Warnings")
        wtext = tk.Text(warn, wrap="word", height=8,
                        background=self._c["surface"],
                        foreground=self._c["text"], relief="solid",
                        borderwidth=1)
        wtext.pack(fill="both", expand=True, padx=6, pady=6)
        if self.ctx.overflow:
            wtext.insert("end", "This task's input is larger than the "
                                "model's context (RUN-CONTEXT-OVERFLOW "
                                "would stop it).\n\n")
        if self.ctx.warnings:
            for w in self.ctx.warnings:
                wtext.insert("end", f"⚠ {w}\n")
        if self.ctx.compaction_steps:
            wtext.insert("end", "\nCompaction that would apply:\n")
            for step in self.ctx.compaction_steps:
                wtext.insert("end", f"· {step}\n")
        if not (self.ctx.overflow or self.ctx.warnings
                or self.ctx.compaction_steps):
            wtext.insert("end", "No warnings. The input fits with room "
                                "to spare.")
        wtext.configure(state="disabled")

    def on_copy(self) -> None:
        parts = []
        for m in self.ctx.messages:
            parts.append(f"[{SECTION_LABELS.get(m.role, m.role)}]\n"
                         f"{m.content}")
        payload = "\n\n".join(parts)
        self.clipboard_clear()
        self.clipboard_append(payload)
        self.app.live.configure(text="Context copied to the clipboard")
