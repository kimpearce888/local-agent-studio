"""S6 Run view (RUN-5, RUN-6, OBS-2, OBS-3): live execution.

Left rail with per-task status and elapsed time; Overview, Transcript,
Handoff and Files tabs; Pause after this task, Pause now, Cancel and
Resume controls; the budget meter; and the completion card.  Approval
gates (S7) and failures (S8) open as dialogs from here.
"""

from __future__ import annotations

import json
import os
import time
import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import BudgetMeter, StatusChip, info_dialog


class RunViewScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self.run_id: str | None = None
        self._selected_task: str | None = None
        self._rendering = False
        self._auto_scroll = tk.BooleanVar(value=True)
        self._build()

    # -- layout --------------------------------------------------------------

    def _build(self) -> None:
        # Header: title, status, controls, budget meter
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 6))
        self.title_lbl = ttk.Label(head, text="Run", style="Title.TLabel")
        self.title_lbl.pack(side="left")
        self.chip = StatusChip(head, self.theme, "queued")
        self.chip.pack(side="left", padx=12)
        self.meter = BudgetMeter(head, self.theme)
        self.meter.pack(side="left", padx=12)
        self.controls = ttk.Frame(head)
        self.controls.pack(side="right")
        self.pause_after_btn = ttk.Button(
            self.controls, text="Pause after this task",
            command=self._pause_after)
        self.pause_after_btn.pack(side="left", padx=3)
        self.pause_now_btn = ttk.Button(
            self.controls, text="Pause now", command=self._pause_now)
        self.pause_now_btn.pack(side="left", padx=3)
        self.cancel_btn = ttk.Button(
            self.controls, text="Cancel run", style="Danger.TButton",
            command=self._cancel)
        self.cancel_btn.pack(side="left", padx=3)
        self.resume_btn = ttk.Button(
            self.controls, text="Resume", style="Primary.TButton",
            command=self._resume)
        self.resume_btn.pack(side="left", padx=3)
        self.resume_btn.pack_forget()

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        # Left rail: task list
        rail = ttk.Frame(body)
        body.add(rail, weight=0)
        ttk.Label(rail, text="Tasks", style="Heading.TLabel").pack(
            anchor="w", padx=6, pady=(4, 2))
        self.rail = ttk.Treeview(rail, columns=("status", "elapsed"),
                                 show="tree", selectmode="browse",
                                 height=16)
        self.rail.column("#0", width=140, anchor="w")
        self.rail.column("status", width=80, anchor="w")
        self.rail.column("elapsed", width=60, anchor="e")
        self.rail.heading("#0", text="Task")
        self.rail.heading("status", text="Status")
        self.rail.heading("elapsed", text="Elapsed")
        self.rail.pack(fill="y", expand=True, padx=4, pady=4)
        self.rail.bind("<<TreeviewSelect>>", lambda e: self._show_task())

        # Main tabs
        tabs = ttk.Notebook(body)
        body.add(tabs, weight=1)
        self.tabs = tabs

        # Overview
        over = ttk.Frame(tabs)
        tabs.add(over, text="Overview")
        self.progress_lbl = ttk.Label(over, text="", font=self.theme.font(2))
        self.progress_lbl.pack(anchor="w", padx=10, pady=(10, 4))
        self.progress_bar = ttk.Progressbar(over, mode="determinate")
        self.progress_bar.pack(fill="x", padx=10)
        self.overview = ttk.Label(over, text="", style="Muted.TLabel",
                                  wraplength=520, justify="left")
        self.overview.pack(anchor="w", padx=10, pady=8)

        # Transcript
        tr = ttk.Frame(tabs)
        tabs.add(tr, text="Transcript")
        bar = ttk.Frame(tr)
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Auto-scroll",
                        variable=self._auto_scroll).pack(side="left",
                                                         padx=8, pady=4)
        self.tr_task_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.tr_task_lbl.pack(side="left", padx=10)
        self.transcript = tk.Text(tr, wrap="word",
                                  background=self.theme.c["surface"],
                                  foreground=self.theme.c["text"],
                                  insertbackground=self.theme.c["text"],
                                  relief="solid", borderwidth=1,
                                  state="disabled",
                                  font=self.theme.font(-1))
        self.transcript.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Handoff
        ho = ttk.Frame(tabs)
        tabs.add(ho, text="Handoff")
        self.handoff = tk.Text(ho, wrap="word",
                               background=self.theme.c["surface"],
                               foreground=self.theme.c["text"],
                               relief="solid", borderwidth=1,
                               state="disabled",
                               font=self.theme.font(-1))
        self.handoff.pack(fill="both", expand=True, padx=6, pady=6)

        # Files
        files = ttk.Frame(tabs)
        tabs.add(files, text="Files")
        self.files_tree = ttk.Treeview(files, show="tree")
        self.files_tree.pack(fill="both", expand=True, padx=6, pady=6)
        self.files_tree.bind("<Double-Button-1>", self._preview_file)

        # Completion card (replaces Overview content when finished)
        self.completion = ttk.Frame(over)

    def focus_regions(self) -> list:
        return [self.rail, self.tabs]

    # -- lifecycle -------------------------------------------------------------

    def on_show(self, run_id: str = "", resume: bool = False, **_ctx) -> None:
        if run_id:
            self.run_id = run_id
        if self.run_id:
            self.refresh_from_state()
            if resume:
                self._resume()

    # -- state refresh -----------------------------------------------------------

    def refresh_from_state(self) -> None:
        if not self.run_id:
            return
        if self._rendering:
            return              # rail rebuild must not re-fire selection
        self._rendering = True
        try:
            self._refresh_inner()
        finally:
            self._rendering = False

    def _refresh_inner(self) -> None:
        try:
            state = self.app.engine.state(self.run_id)
        except Exception:                              # noqa: BLE001
            return
        self.title_lbl.configure(text=state.title or "Run")
        self.chip.set(state.status)
        caps = state.caps or {}
        self.meter.update_usage(
            state.tokens_used(),
            int(caps.get("tokens") or 0) or 200_000)
        self._render_rail(state)
        self._render_overview(state)
        self._render_transcript()
        self._render_handoff(state)
        self._render_files()
        self._render_completion(state)
        if state.status in ("paused", "awaiting_approval", "failed"):
            self.resume_btn.pack(side="left", padx=3)
        else:
            self.resume_btn.pack_forget()

    def _render_rail(self, state) -> None:
        self.rail.delete(*self.rail.get_children())
        for t in state.plan.tasks:
            ts = state.tasks.get(t.id)
            status = ts.state if ts else "pending"
            icon, text, _role = self.theme.status_appearance(status)
            elapsed = ""
            if status == "running" and ts.attempts:
                try:
                    from datetime import datetime
                    started = datetime.fromisoformat(
                        state.events[-1].ts)
                    elapsed = "…"
                except Exception:                      # noqa: BLE001
                    elapsed = ""
            self.rail.insert("", "end", iid=t.id,
                             text=f"{t.id}  {t.title[:18]}",
                             values=(f"{icon} {text}", elapsed))
        if self._selected_task and self.rail.exists(self._selected_task):
            self.rail.selection_set(self._selected_task)
            self.rail.see(self._selected_task)
        else:
            first = state.plan.tasks[0].id if state.plan.tasks else None
            if first:
                self.rail.selection_set(first)
                self._selected_task = first

    def _render_overview(self, state) -> None:
        done = sum(1 for ts in state.tasks.values()
                   if ts.state == "succeeded")
        total = len(state.plan.tasks)
        self.progress_bar.configure(maximum=max(1, total), value=done)
        self.progress_lbl.configure(text=f"{done} of {total} tasks done")
        tokens = state.tokens_used()
        usage_bits = [f"{tokens} tokens used"]
        attempts = sum(len(ts.attempts) for ts in state.tasks.values())
        usage_bits.append(f"{attempts} attempts")
        skipped = sum(1 for ts in state.tasks.values()
                      if ts.state == "skipped")
        if skipped:
            usage_bits.append(f"{skipped} skipped")
        self.overview.configure(text=" · ".join(usage_bits))

    def _render_transcript(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        task_id = self._selected_task
        if not self.run_id or not task_id:
            self.tr_task_lbl.configure(text="Select a task in the rail")
            self.transcript.configure(state="disabled")
            return
        self.tr_task_lbl.configure(text=f"Transcript of {task_id}")
        events = self.app.store.events(self.run_id)
        for ev in events:
            p = ev.payload
            if p.get("task_id") != task_id:
                continue
            if ev.type == "message.added":
                role = p.get("role", "?")
                content = str(p.get("content", ""))
                label = {"assistant": "Model", "user": "Request",
                         "system": "System", "tool": "Tool result "
                         "(UNTRUSTED)"}.get(role, role)
                tag = "untrusted" if role == "tool" else role
                self.transcript.insert("end", f"[{label}]\n", tag)
                self.transcript.insert("end", content + "\n\n", tag)
            elif ev.type == "tool.called":
                self.transcript.insert(
                    "end", f"▸ Tool call: {p.get('tool', '?')} "
                           f"({json.dumps(p.get('arguments', {}))[:160]})\n",
                    "tool")
            elif ev.type == "tool.result":
                self.transcript.insert(
                    "end", f"  ↳ result: "
                           f"{str(p.get('result', ''))[:240]}\n", "untrusted")
            elif ev.type == "reasoning.added":
                self.transcript.insert(
                    "end", "▸ Reasoning (not passed on)\n", "reasoning")
        base = self.theme.c
        self.transcript.tag_configure("untrusted",
                                      foreground=base["warning"])
        self.transcript.tag_configure("tool", foreground=base["info"])
        self.transcript.tag_configure("reasoning",
                                      foreground=base["muted"])
        if self._auto_scroll.get():
            self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _render_handoff(self, state) -> None:
        self.handoff.configure(state="normal")
        self.handoff.delete("1.0", "end")
        task_id = self._selected_task
        ts = state.tasks.get(task_id) if task_id else None
        if ts is not None and ts.handoff:
            h = ts.handoff
            self.handoff.insert("end", f"Task {task_id}\n\n", "h")
            for key in ("summary", "status"):
                if h.get(key):
                    self.handoff.insert("end", f"{key}: {h[key]}\n")
            for fact in h.get("key_facts", [])[:10]:
                self.handoff.insert(
                    "end", f"· {fact.get('fact', '')} "
                           f"(source: {fact.get('source', '?')})\n")
            cons = h.get("binding_constraints", [])
            if cons:
                self.handoff.insert("end", "\nBinding constraints:\n")
                for c in cons:
                    self.handoff.insert("end", f"· {c.get('text', c)}\n")
        else:
            self.handoff.insert("end", "No handoff yet for this task.")
        self.handoff.tag_configure("h", font=self.theme.font(1, "bold"))
        self.handoff.configure(state="disabled")

    def _render_files(self) -> None:
        self.files_tree.delete(*self.files_tree.get_children())
        if not self.run_id:
            return
        root = os.path.join(self.app.data, "workspaces", self.run_id)
        if not os.path.isdir(root):
            return
        def walk(dirpath, parent):
            for name in sorted(os.listdir(dirpath)):
                path = os.path.join(dirpath, name)
                if os.path.isdir(path):
                    node = self.files_tree.insert(parent, "end",
                                                  text="▸ " + name,
                                                  open=False, values=(path,))
                    walk(path, node)
                else:
                    self.files_tree.insert(parent, "end",
                                           text=f"· {name} "
                                                f"({os.path.getsize(path)} B)",
                                           values=(path,))
        walk(root, "")

    def _preview_file(self, _event) -> None:
        sel = self.files_tree.selection()
        if not sel:
            return
        values = self.files_tree.item(sel[0], "values")
        if not values:
            return
        path = values[0]
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read(8000)
            except OSError as exc:
                content = f"(cannot read: {exc})"
            info_dialog(self, os.path.basename(path), content or "(empty)")

    def _render_completion(self, state) -> None:
        for w in self.completion.winfo_children():
            w.destroy()
        if state.status not in ("succeeded", "failed", "cancelled"):
            self.completion.pack_forget()
            return
        c = self.theme.c
        card = tk.Frame(self.completion, background=c["success_soft"]
                        if state.status == "succeeded" else c["danger_soft"],
                        padx=14, pady=10)
        card.pack(fill="x", padx=10, pady=8)
        tk.Label(card, text="Run complete", background=card.cget("background"),
                 foreground=c["text"], font=self.theme.font(2, "bold")).pack(
            anchor="w")
        done = sum(1 for ts in state.tasks.values()
                   if ts.state == "succeeded")
        text = (f"{state.status} · {done}/{len(state.plan.tasks)} tasks · "
                f"{state.tokens_used()} tokens")
        out = next((t for t in state.plan.tasks
                    if t.type == "output" and
                    state.tasks.get(t.id) and
                    state.tasks[t.id].handoff), None)
        if out is None:
            out = next((t for t in reversed(state.plan.tasks)
                        if state.tasks.get(t.id) and
                        state.tasks[t.id].handoff), None)
        if out is not None:
            text += f" · result in {out.id}"
        tk.Label(card, text=text, background=card.cget("background"),
                 foreground=c["text"]).pack(anchor="w", pady=(4, 6))
        row = ttk.Frame(card)
        row.pack(anchor="w")
        ttk.Button(row, text="Export…",
                   command=lambda: self.app.open_export(self.run_id)).pack(
            side="left", padx=(0, 6))
        ttk.Button(row, text="Save as template",
                   command=self._save_template).pack(side="left")
        if out is not None:
            def open_result():
                self._selected_task = out.id
                self.refresh_from_state()
                self.tabs.select(2)
            ttk.Button(row, text="Open the result",
                       command=open_result).pack(side="left", padx=6)
        self.completion.pack(fill="x")

    def _save_template(self) -> None:
        if not self.run_id:
            return
        first = self.app.store.first_event(self.run_id)
        plan_obj = first.payload.get("plan")
        if not plan_obj:
            return
        from las.modules.library import save_template
        state = self.app.engine.state(self.run_id)
        try:
            save_template(self.app.store, plan_obj, state.title,
                          f"From run {self.run_id}")
            self.app.live.configure(text="Template saved")
        except Exception as exc:                          # noqa: BLE001
            info_dialog(self, "Template", str(exc))

    # -- controls -----------------------------------------------------------------

    def _pause_after(self) -> None:
        """Pause after the currently selected (or running) task."""
        if self.app.session is None:
            return
        state = self.app.engine.state(self.run_id)
        running = next((tid for tid, ts in state.tasks.items()
                        if ts.state == "running"), None)
        target = self._selected_task or running
        if target:
            self.app.session.pause_after(target)
            self.app.live.configure(text=f"Pausing after {target}")

    def _pause_now(self) -> None:
        if self.app.session is not None:
            self.app.session.request_pause_now()
            self.app.live.configure(
                text="Pausing now - the current attempt restarts on resume")

    def _cancel(self) -> None:
        if self.app.session is not None:
            self.app.session.request_cancel()
            self.app.session.decide("cancel")
        else:
            self.app.live.configure(text="No active run")

    def _resume(self) -> None:
        if self.app.session is not None:
            self.app.session.decide("resume")
            self.app.live.configure(text="Resuming")
            return
        # Resume a paused/failed run from history: a fresh session with
        # the ORIGINAL frozen configuration (item 18) - the engine
        # restores models and config from run.created.
        from las.app.gui.runner import RunSession
        self.app.start_run(self.run_id)

    # -- session events ------------------------------------------------------------

    def on_session_event(self, kind: str, payload: dict) -> None:
        if kind in ("results", "started", "info"):
            self.refresh_from_state()
        elif kind == "waiting":
            reason = payload.get("reason")
            if reason == "gate":
                self._open_gate(payload)
            elif reason == "failure":
                self._open_failure(payload)
            else:
                self.refresh_from_state()
        elif kind == "finished":
            self.refresh_from_state()
            status = payload.get("kind", "")
            self.app.live.say(
                "Run succeeded" if status == "run_succeeded"
                else f"Run {status.replace('run_', '')}", assertive=True)

    def _open_gate(self, payload: dict) -> None:
        from las.app.gui.s7_gate import GateDialog
        GateDialog(self, self.app, self.run_id, payload.get("task_id", ""),
                   self.app.session)

    def _open_failure(self, payload: dict) -> None:
        from las.app.gui.s8_failure import FailureDialog
        FailureDialog(self, self.app, self.run_id, payload, self.app.session)

    def _show_task(self) -> None:
        # Restoring the selection after a rebuild re-fires this event;
        # only a genuine change refreshes (else refresh ping-pongs).
        sel = self.rail.selection()
        if sel and sel[0] != self._selected_task:
            self._selected_task = sel[0]
            self.refresh_from_state()
