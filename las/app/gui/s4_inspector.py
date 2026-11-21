"""S4 inspector (spec table): eight tabs over the selected task."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from las.modules.security import (
    KNOWN_TOOLS, OFF_BY_DEFAULT_TOOLS, TOOL_DEFS, TOOL_FLAGS,
)
from las.shared.plan_contract import INSTRUCTIONS_MAX, INSTRUCTIONS_MIN, TITLE_MAX

RISK_LABELS = {
    "run_command": "B sensitive access · C external effect",
    "web_fetch": "A untrusted input · C external effect",
}


class Inspector(ttk.Frame):
    """Right-hand inspector, 360 px, collapsible."""

    def __init__(self, parent, studio):
        super().__init__(parent)
        self.studio = studio
        self.theme = studio.theme
        self._collapsed = False
        self._task_id = None

        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Inspector", style="Heading.TLabel").pack(
            side="left")
        self.toggle_btn = ttk.Button(head, text="Hide", width=5,
                                     command=self.toggle)
        self.toggle_btn.pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self._build_task_tab()
        self._build_inputs_tab()
        self._build_constraints_tab()
        self._build_model_tab()
        self._build_tools_tab()
        self._build_limits_tab()
        self._build_context_tab()
        self._build_result_tab()

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.tabs.pack_forget()
            self.toggle_btn.configure(text="Show")
            self.configure(width=90)
        else:
            self.tabs.pack(fill="both", expand=True)
            self.toggle_btn.configure(text="Hide")
            self.configure(width=360)
        self.studio.main.sashpos(0, 10000 if self._collapsed else -360)

    def focus_tab(self, task_id: str | None = None) -> None:
        if task_id:
            self.studio.selected = task_id
        self.refresh()
        self.tabs.select(0)
        self.tabs.focus_set()

    # -- current task ----------------------------------------------------------

    def _task(self):
        plan = self.studio.plan
        if plan is None:
            return None
        return next((t for t in plan.tasks if t.id == self.studio.selected),
                    None)

    def refresh(self) -> None:
        task = self._task()
        if task is None:
            self._task_id = None
            for setter in self._clearers:
                setter()
            for box in (self.inputs_box, self.cons_box, self.tools_box):
                for w in box.winfo_children():
                    w.destroy()
            self.result_lbl.configure(text="Select a task to inspect it.")
            self.transcript_btn.pack_forget()
            self.ctx_estimate.configure(text="")
            return
        if task.id != self._task_id:
            self._load(task)
            self._task_id = task.id
        else:
            self._load(task, keep_focus=True)

    # -- Task tab ---------------------------------------------------------------

    def _build_task_tab(self) -> None:
        self._clearers = []
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Task")
        self.title_var = tk.StringVar()
        ttk.Label(tab, text="Title").pack(anchor="w", padx=8, pady=(8, 0))
        self.title_entry = ttk.Entry(tab, textvariable=self.title_var)
        self.title_entry.pack(fill="x", padx=8)
        self.title_entry.bind(
            "<FocusOut>", lambda e: self._set_field("title"))
        self._counter = ttk.Label(tab, text="", style="Muted.TLabel")
        self._counter.pack(anchor="e", padx=10)

        ttk.Label(tab, text="Instructions").pack(anchor="w", padx=8)
        self.instructions = tk.Text(tab, wrap="word", height=8,
                                    background=self.theme.c["surface"],
                                    foreground=self.theme.c["text"],
                                    insertbackground=self.theme.c["text"],
                                    relief="solid", borderwidth=1,
                                    font=self.theme.font(-1))
        self.instructions.pack(fill="both", expand=True, padx=8, pady=4)
        self.instructions.bind("<FocusOut>", lambda e: self._set_field(
            "instructions"))
        ttk.Label(tab, text="Expected output (optional)").pack(
            anchor="w", padx=8)
        self.expected = tk.Text(tab, wrap="word", height=3,
                                background=self.theme.c["surface"],
                                foreground=self.theme.c["text"],
                                insertbackground=self.theme.c["text"],
                                relief="solid", borderwidth=1,
                                font=self.theme.font(-1))
        self.expected.pack(fill="x", padx=8, pady=(0, 8))
        self.expected.bind("<FocusOut>", lambda e: self._set_field(
            "expected_output"))
        self._clearers.append(self._clear_task_tab)

    def _clear_task_tab(self) -> None:
        self.title_var.set("")
        self.instructions.delete("1.0", "end")
        self.expected.delete("1.0", "end")
        self._counter.configure(text="")

    def _load(self, task, keep_focus: bool = False) -> None:
        focus = self.focus_get()
        if focus is not self.title_entry:
            self.title_var.set(task.title)
        if focus is not self.instructions:
            self.instructions.delete("1.0", "end")
            self.instructions.insert("1.0", task.instructions)
        if focus is not self.expected:
            self.expected.delete("1.0", "end")
            self.expected.insert("1.0", task.expected_output or "")
        n = len(task.instructions)
        kind_ok = task.type in ("gate", "note")
        if kind_ok or INSTRUCTIONS_MIN <= n <= INSTRUCTIONS_MAX:
            self._counter.configure(
                text=f"{n} characters" + ("" if kind_ok or n >= INSTRUCTIONS_MIN
                                          else f" ({INSTRUCTIONS_MIN} minimum)"))
        else:
            self._counter.configure(
                text=f"{n} characters "
                     f"({INSTRUCTIONS_MIN} to {INSTRUCTIONS_MAX})")
        self._load_inputs(task)
        self._load_constraints(task)
        self._load_model(task)
        self._load_tools(task)
        self._load_limits(task)
        self._load_context(task)
        self._load_result(task)

    def _set_field(self, field: str) -> None:
        task = self._task()
        if task is None:
            return
        if field == "title":
            value = self.title_var.get().strip()[:TITLE_MAX]
            if value and value != task.title:
                self._mutate_field(task.id, field, value)
        elif field == "instructions":
            value = self.instructions.get("1.0", "end-1c")
            if value != task.instructions:
                self._mutate_field(task.id, field, value)
        elif field == "expected_output":
            value = self.expected.get("1.0", "end-1c")
            if value != (task.expected_output or ""):
                self._mutate_field(task.id, field, value)

    def _mutate_field(self, task_id: str, field: str, value) -> None:
        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task_id)
            setattr(t, field, value)
        self.studio.mutate(f"Edited {field} of {task_id}", apply)

    # -- Inputs tab ---------------------------------------------------------------

    def _build_inputs_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Inputs")
        self.inputs_lbl = ttk.Label(
            tab, text="Checklist of earlier tasks; each row previews that "
                      "task's handoff summary once it exists.",
            style="Muted.TLabel", wraplength=330)
        self.inputs_lbl.pack(anchor="w", padx=8, pady=8)
        self.inputs_box = ttk.Frame(tab)
        self.inputs_box.pack(fill="both", expand=True)

    def _load_inputs(self, task) -> None:
        for w in self.inputs_box.winfo_children():
            w.destroy()
        plan = self.studio.plan
        ids = [t.id for t in plan.tasks]
        earlier = ids[:ids.index(task.id)] if task.id in ids else []
        if not earlier:
            ttk.Label(self.inputs_box, text="(first task - no inputs)",
                      style="Muted.TLabel").pack(anchor="w", padx=8)
        handoffs = getattr(self.studio, "_handoff_summaries", {})
        for tid in earlier:
            var = tk.BooleanVar(value=tid in task.needs)
            cb = ttk.Checkbutton(
                self.inputs_box, text=f"{tid} - needs this",
                variable=var,
                command=lambda t=tid, v=var: self._toggle_need(task.id, t,
                                                               v.get()))
            cb.pack(anchor="w", padx=8, pady=1)
            summary = handoffs.get(tid)
            if summary:
                ttk.Label(self.inputs_box, text=f"↳ {summary[:70]}",
                          style="Muted.TLabel", wraplength=320).pack(
                    anchor="w", padx=24)

    def _toggle_need(self, task_id: str, dep: str, on: bool) -> None:
        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task_id)
            if on and dep not in t.needs:
                t.needs.append(dep)
            elif not on and dep in t.needs:
                t.needs.remove(dep)
        self.studio.mutate(
            f"{'Added' if on else 'Removed'} input {dep} for {task_id}",
            apply)

    # -- Constraints tab -------------------------------------------------------------

    def _build_constraints_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Constraints")
        self.cons_box = ttk.Frame(tab)
        self.cons_box.pack(fill="both", expand=True)
        add_row = ttk.Frame(tab)
        add_row.pack(fill="x", pady=4)
        self.new_con = ttk.Entry(add_row)
        self.new_con.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(add_row, text="Add", command=self._add_constraint).pack(
            side="right", padx=8)

    def _load_constraints(self, task) -> None:
        for w in self.cons_box.winfo_children():
            w.destroy()
        plan = self.studio.plan
        mine = set(task.constraints)
        for c in plan.constraints:
            inherited = c.authority != "user" or c.id not in mine
            applies = c.applies_to(task.id)
            if c.id in mine or applies:
                label = f"{c.id}: {c.text[:60]}"
                extra = ""
                if c.prerequisite:
                    extra = f" (prerequisite: {c.prerequisite})"
                row = tk.Frame(self.cons_box,
                               background=self.theme.c["surface2"] if inherited
                               else self.theme.c["bg"])
                row.pack(fill="x", padx=8, pady=1)
                var = tk.BooleanVar(value=c.id in mine)
                state = "disabled" if inherited else "normal"
                ttk.Checkbutton(
                    row, text=label + extra, variable=var, state=state,
                    command=lambda cid=c.id, v=var:
                        self._toggle_task_constraint(task.id, cid,
                                                     v.get())).pack(
                    anchor="w")
                if inherited:
                    ttk.Label(row, text="inherited",
                              style="Muted.TLabel").pack(side="right")

    def _toggle_task_constraint(self, task_id: str, cid: str, on: bool) -> None:
        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task_id)
            if on and cid not in t.constraints:
                t.constraints.append(cid)
            elif not on and cid in t.constraints:
                t.constraints.remove(cid)
        self.studio.mutate(f"Constraint {cid} for {task_id}", apply)

    def _add_constraint(self) -> None:
        from las.shared.plan_contract import Constraint
        text = self.new_con.get().strip()
        task = self._task()
        if not text or task is None or self.studio.plan is None:
            return
        import time as _t
        cid = f"c{len(self.studio.plan.constraints) + 1}-{_t.strftime('%H%M%S')}"
        target = task.id

        def apply(plan):
            plan.constraints.append(Constraint(
                id=cid, text=text, prerequisite="", authority="user",
                fallback="", scope=[target], origin="user"))
            t = next(t for t in plan.tasks if t.id == target)
            t.constraints.append(cid)
        self.studio.mutate(f"Added constraint {cid}", apply)
        self.new_con.delete(0, "end")

    # -- Model tab -------------------------------------------------------------------

    def _build_model_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Model")
        self.role_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.temp_var = tk.StringVar()
        self.topp_var = tk.StringVar()
        self.maxtok_var = tk.StringVar()
        self.seed_var = tk.StringVar()
        self.stop_var = tk.StringVar()
        self.ctx_var = tk.StringVar()
        rows = [("Role (planner, worker, collector)", self.role_var,
                 ("planner", "worker", "collector")),
                ("Model override", self.model_var, None),
                ("Temperature", self.temp_var, None),
                ("top_p", self.topp_var, None),
                ("Max tokens", self.maxtok_var, None),
                ("Seed", self.seed_var, None),
                ("Stop sequences (comma separated)", self.stop_var, None),
                ("Context length", self.ctx_var, None)]
        for i, (label, var, values) in enumerate(rows):
            ttk.Label(tab, text=label).grid(row=i, column=0, sticky="w",
                                            padx=8, pady=3)
            if values:
                box = ttk.Combobox(tab, textvariable=var, values=list(values),
                                   state="readonly", width=16)
            else:
                box = ttk.Entry(tab, textvariable=var, width=18)
            box.grid(row=i, column=1, sticky="w", padx=8, pady=3)
            box.bind("<FocusOut>", lambda e: self._set_model_fields())
        tab.columnconfigure(1, weight=1)

    def _load_model(self, task) -> None:
        self.role_var.set(task.role)
        self.model_var.set((task.model or {}).get("model_id", ""))
        p = task.params or {}
        self.temp_var.set(str(p.get("temperature", "")))
        self.topp_var.set(str(p.get("top_p", "")))
        self.maxtok_var.set(str(p.get("max_tokens", "")))
        self.seed_var.set(str(p.get("seed", "")))
        self.stop_var.set(",".join(p.get("stop", []) or []))
        self.ctx_var.set(str(p.get("context_length", "")))

    def _set_model_fields(self) -> None:
        task = self._task()
        if task is None:
            return
        model_id = self.model_var.get().strip()
        params: dict = {}
        for key, var, cast in [
                ("temperature", self.temp_var, float),
                ("top_p", self.topp_var, float),
                ("max_tokens", self.maxtok_var, int),
                ("seed", self.seed_var, int),
                ("context_length", self.ctx_var, int)]:
            value = var.get().strip()
            if value:
                try:
                    params[key] = cast(value)
                except ValueError:
                    pass
        stops = [s.strip() for s in self.stop_var.get().split(",")
                 if s.strip()]
        if stops:
            params["stop"] = stops

        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task.id)
            t.role = self.role_var.get() or t.role
            t.model = ({"provider_id": (t.model or {}).get("provider_id", ""),
                        "model_id": model_id} if model_id else None)
            t.params = params
        self.studio.mutate(f"Model settings for {task.id}", apply)

    # -- Tools tab ----------------------------------------------------------------------

    def _build_tools_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Tools")
        self.tools_box = ttk.Frame(tab)
        self.tools_box.pack(fill="both", expand=True)
        self.tool_flags_lbl = ttk.Label(tab, text="", style="Muted.TLabel",
                                        wraplength=330)
        self.tool_flags_lbl.pack(anchor="w", padx=8, pady=4)

    def _load_tools(self, task) -> None:
        for w in self.tools_box.winfo_children():
            w.destroy()
        for name in sorted(KNOWN_TOOLS):
            var = tk.BooleanVar(value=name in task.tools)
            risk = RISK_LABELS.get(name, "workspace only")
            row = ttk.Frame(self.tools_box)
            row.pack(fill="x", padx=8, pady=1)
            ttk.Checkbutton(
                row, text=name, variable=var,
                command=lambda n=name, v=var: self._toggle_tool(
                    task.id, n, v.get())).pack(side="left")
            ttk.Label(row, text=risk, style="Muted.TLabel").pack(
                side="right")
            if name in OFF_BY_DEFAULT_TOOLS:
                ttk.Label(row, text="off by default",
                          style="Muted.TLabel").pack(side="right", padx=6)
        flags = self.studio.flags_map().get(task.id, (False, False, False))
        self.tool_flags_lbl.configure(
            text=f"Resulting flags: A untrusted input "
                 f"{'set' if flags[0] else 'clear'}, B sensitive access "
                 f"{'set' if flags[1] else 'clear'}, C external effect "
                 f"{'set' if flags[2] else 'clear'}")

    def _toggle_tool(self, task_id: str, tool: str, on: bool) -> None:
        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task_id)
            if on and tool not in t.tools:
                t.tools.append(tool)
            elif not on and tool in t.tools:
                t.tools.remove(tool)
        self.studio.mutate(
            f"{'Granted' if on else 'Revoked'} {tool} for {task_id}", apply)

    # -- Limits tab ---------------------------------------------------------------------

    def _build_limits_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Limits")
        self.limits_vars = {}
        for i, (key, label, default) in enumerate([
                ("retries", "Retries (default 2)", "2"),
                ("timeout", "Timeout (seconds)", "300"),
                ("max_tokens", "Token budget", "8192"),
                ("max_turns", "Maximum turns", "12"),
                ("max_tool_calls", "Maximum tool calls", "20")]):
            var = tk.StringVar()
            self.limits_vars[key] = var
            ttk.Label(tab, text=label).grid(row=i, column=0, sticky="w",
                                            padx=8, pady=3)
            box = ttk.Entry(tab, textvariable=var, width=10)
            box.grid(row=i, column=1, sticky="w", padx=8, pady=3)
            box.bind("<FocusOut>", lambda e: self._set_limits())

    def _load_limits(self, task) -> None:
        limits = task.limits or {}
        for key, var in self.limits_vars.items():
            var.set(str(limits.get(key, "")))

    def _set_limits(self) -> None:
        task = self._task()
        if task is None:
            return
        limits: dict = {}
        for key, var in self.limits_vars.items():
            value = var.get().strip()
            if value:
                try:
                    limits[key] = int(value)
                except ValueError:
                    try:
                        limits[key] = float(value)
                    except ValueError:
                        continue

        def apply(plan):
            t = next(t for t in plan.tasks if t.id == task.id)
            t.limits = limits
        self.studio.mutate(f"Limits for {task.id}", apply)

    # -- Context tab ---------------------------------------------------------------------

    def _build_context_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Context")
        ttk.Button(tab, text="Preview context",
                   style="Primary.TButton",
                   command=self._preview).pack(pady=(12, 6))
        self.ctx_estimate = ttk.Label(tab, text="", style="Muted.TLabel",
                                      wraplength=320, justify="left")
        self.ctx_estimate.pack(anchor="w", padx=8)

    def _load_context(self, task) -> None:
        from las.modules.context import estimate_tokens
        from las.modules.planquality.linting import _predict
        try:
            predicted = _predict(self.studio.plan) if self.studio.plan else {}
            ctx = predicted.get(task.id)
            if ctx is not None:
                self.ctx_estimate.configure(
                    text=f"Estimated input: {ctx.token_estimate} tokens of "
                         f"{ctx.available_input} available "
                         f"({int(ctx.token_estimate * 100 // max(1, ctx.available_input))}%).")
            else:
                raise KeyError
        except Exception:                              # noqa: BLE001
            text = task.instructions or ""
            self.ctx_estimate.configure(
                text=f"Rough estimate: {estimate_tokens(text)} tokens of "
                     "instructions (exact counts in Preview).")

    def _preview(self) -> None:
        task = self._task()
        if task is None:
            return
        if self.studio.run_id:
            self.studio.app.open_context_preview(self.studio.run_id,
                                                 task.id)
        else:
            from las.app.gui.s5_context import preview_plan_task
            preview_plan_task(self, self.studio, task)

    # -- Result tab ------------------------------------------------------------------------

    def _build_result_tab(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Result")
        self.result_lbl = ttk.Label(tab, text="", wraplength=320,
                                    justify="left")
        self.result_lbl.pack(anchor="w", padx=8, pady=8)
        self.transcript_btn = ttk.Button(
            tab, text="Open transcript", command=self._open_transcript)
        self.transcript_btn.pack(anchor="w", padx=8)
        self.transcript_btn.pack_forget()

    def _load_result(self, task) -> None:
        run_id = self.studio.run_id
        if not run_id:
            self.result_lbl.configure(
                text="Results appear here while a run is active.")
            self.transcript_btn.pack_forget()
            return
        try:
            state = self.studio.app.engine.state(run_id)
            ts = state.tasks.get(task.id)
            if ts is not None and ts.handoff:
                h = ts.handoff
                lines = [f"Status: {ts.state}",
                         f"Summary: {h.get('summary', '')}",
                         f"Facts: {len(h.get('key_facts', []))}",
                         f"Constraints carried: "
                         f"{len(h.get('binding_constraints', []))}"]
                self.result_lbl.configure(text="\n".join(lines))
                self.transcript_btn.pack(anchor="w", padx=8)
            else:
                self.result_lbl.configure(
                    text=f"Status: {ts.state if ts else 'not started'}")
                self.transcript_btn.pack_forget()
        except Exception:                              # noqa: BLE001
            self.result_lbl.configure(text="(run not available)")

    def _open_transcript(self) -> None:
        if self.studio.run_id and self.studio.selected:
            self.studio.app.open_run(self.studio.run_id)


