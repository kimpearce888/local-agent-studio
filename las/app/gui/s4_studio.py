"""S4 Studio (BLD-1 to BLD-8): the plan editor.

Toolbar, canvas or list view (the accessible equivalent), inspector
(right, 360 px, collapsible), bottom dock (Constraints, Lint, Log),
preflight on Run, continuous autosave, and undo/redo over plan
mutations.  Gate nodes set ``gate_before`` on the next executable
task - the engine's representation - and the studio maintains that
link when they are added, moved or removed.
"""

from __future__ import annotations

import copy
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog

from las.app.gui.widgets import (
    CapabilityBadge, Dialog, confirm, error_dialog, info_dialog,
)
from las.modules.planquality import errors as lint_errors
from las.modules.planquality import lint, validate_plan
from las.modules.security import PermissionBroker, Workspace
from las.shared.plan_contract import (
    Plan, Task, TASK_TYPES, TITLE_MAX, INSTRUCTIONS_MIN, INSTRUCTIONS_MAX,
)
from las.shared.messages import message_with_fix

EXECUTABLE = ("task", "collector", "input", "output")
GATE_LIKE = ("gate", "note")
TYPE_ICONS = {"task": "▶", "collector": "::_:", "gate": "⚑", "input": "→",
              "output": "←", "note": "✎"}


class StudioScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self.plan: Plan | None = None
        self.run_id: str | None = None
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._autosave_job = None
        self._saved_at = ""
        self.selected: str | None = None
        self.view_mode = "canvas"
        self.zoom = 1.0
        self.show_minimap = True
        self._build()

    # -- layout --------------------------------------------------------------

    def _build(self) -> None:
        self._build_toolbar()

        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True)
        self.main = main

        # Centre: canvas / list switch
        from las.app.gui.s4_canvas import CanvasView, ListView
        self.holder = ttk.Frame(main)
        self.canvas_view = CanvasView(self.holder, self)
        self.canvas_view.pack(fill="both", expand=True)
        self.list_view = ListView(self.holder, self)
        main.add(self.holder, weight=1)

        # Inspector (360 px, collapsible)
        from las.app.gui.s4_inspector import Inspector
        self.inspector = Inspector(main, self)
        main.add(self.inspector, weight=0)
        self.inspector.configure(width=360)

        # Bottom dock
        self._build_dock()

        self.bind_shortcuts()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))

        ttk.Button(bar, text="↶ Undo", width=7,
                   command=self.undo).pack(side="left")
        ttk.Button(bar, text="↷ Redo", width=7,
                   command=self.redo).pack(side="left", padx=(2, 8))

        add = tk.Menubutton(bar, text="＋ Add", relief="raised",
                            background=self.theme.c["surface"],
                            foreground=self.theme.c["text"],
                            activebackground=self.theme.c["surface2"],
                            font=self.theme.font(), padx=8, pady=3)
        menu = tk.Menu(add, tearoff=0,
                       background=self.theme.c["surface"],
                       foreground=self.theme.c["text"],
                       activebackground=self.theme.c["accent"],
                       activeforeground=self.theme.c["accent_text"])
        for label, kind in [("Task", "task"), ("Collector", "collector"),
                            ("Approval gate", "gate"), ("Input", "input"),
                            ("Output", "output"), ("Note", "note")]:
            menu.add_command(label=label,
                             command=lambda k=kind: self.add_node(k))
        add.configure(menu=menu)
        add.pack(side="left")

        ttk.Button(bar, text="Auto-layout", command=self.auto_layout).pack(
            side="left", padx=8)
        ttk.Button(bar, text="－", width=3,
                   command=lambda: self._zoom(-0.1)).pack(side="left")
        self.zoom_lbl = ttk.Label(bar, text="100%", style="Muted.TLabel")
        self.zoom_lbl.pack(side="left")
        ttk.Button(bar, text="＋", width=3,
                   command=lambda: self._zoom(0.1)).pack(side="left")
        self.minimap_btn = ttk.Button(
            bar, text="Minimap", command=self._toggle_minimap)
        self.minimap_btn.pack(side="left", padx=(8, 2))
        self.view_btn = ttk.Button(bar, text="List view",
                                   command=self.switch_view)
        self.view_btn.pack(side="left", padx=2)
        ttk.Button(bar, text="Validate", command=self.validate_now).pack(
            side="left", padx=8)

        ttk.Button(bar, text="Plan ▾", command=self._plan_menu).pack(
            side="right", padx=2)
        self.run_btn = ttk.Button(bar, text="▶ Run", style="Primary.TButton",
                                  command=self.run_plan)
        self.run_btn.pack(side="right")
        self.saved_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.saved_lbl.pack(side="right", padx=10)

    def _build_dock(self) -> None:
        dock = ttk.Frame(self)
        dock.pack(fill="x", pady=(4, 0))
        self.dock = ttk.Notebook(dock)
        self.dock.pack(fill="x")

        # Constraints tab
        cons = ttk.Frame(self.dock)
        self.dock.add(cons, text="Constraints")
        cols = ("id", "text", "prerequisite", "authority", "fallback",
                "applies_to")
        self.cons_tree = ttk.Treeview(cons, columns=cols, show="headings",
                                      height=4)
        for cid, head, w in [("id", "ID", 44), ("text", "Text", 320),
                             ("prerequisite", "Prerequisite", 110),
                             ("authority", "Authority", 80),
                             ("fallback", "Fallback", 90),
                             ("applies_to", "Applies to", 160)]:
            self.cons_tree.heading(cid, text=head)
            self.cons_tree.column(cid, width=w, anchor="w")
        self.cons_tree.pack(fill="x", padx=4, pady=4)

        # Lint tab
        lintf = ttk.Frame(self.dock)
        self.dock.add(lintf, text="Lint")
        cols = ("rule", "severity", "message", "task")
        self.lint_tree = ttk.Treeview(lintf, columns=cols, show="headings",
                                      height=4)
        for cid, head, w in [("rule", "Rule", 56),
                             ("severity", "Severity", 76),
                             ("message", "Message", 420),
                             ("task", "Task", 60)]:
            self.lint_tree.heading(cid, text=head)
            self.lint_tree.column(cid, width=w, anchor="w")
        self.lint_tree.pack(fill="x", padx=4, pady=4)
        self.lint_tree.bind("<Double-Button-1>", self._focus_finding)

        # Log tab
        logf = ttk.Frame(self.dock)
        self.dock.add(logf, text="Log")
        self.log_text = tk.Text(logf, height=4, wrap="word",
                                background=self.theme.c["surface"],
                                foreground=self.theme.c["text"],
                                relief="solid", borderwidth=1,
                                state="disabled",
                                font=self.theme.font(-1))
        self.log_text.pack(fill="x", padx=4, pady=4)

    def bind_shortcuts(self) -> None:
        """Screen-local bindings; the global ones (Ctrl+Enter, view
        switch) are bound by the app on the shared root."""
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-Shift-Z>", lambda e: self.redo())

    def focus_regions(self) -> list:
        return [self.canvas_view if self.view_mode == "canvas"
                else self.list_view.tree, self.inspector, self.dock]

    # -- plan lifecycle --------------------------------------------------------

    def load_plan(self, plan: Plan, run_id: str | None = None,
                  lint_on_open: bool = False) -> None:
        self.plan = plan
        self.run_id = run_id
        self._undo.clear()
        self._redo.clear()
        self.selected = None
        self.render_all()
        if lint_on_open:
            self.dock.select(1)          # the Lint tab
            self.app.live.configure(
                text="The plan has errors - they are listed in the Lint tab")
        self.log(f"Opened plan {plan.id} ({len(plan.tasks)} tasks)")

    def on_show(self, **ctx) -> None:
        if self.plan is None and ctx.get("plan"):
            self.load_plan(ctx["plan"])

    # -- mutations (all go through here: undo, autosave, render) --------------

    def mutate(self, description: str, fn) -> None:
        """Apply a plan mutation with undo tracking and autosave."""
        if self.plan is None:
            return
        self._undo.append(self.plan.to_obj())
        self._redo.clear()
        fn(self.plan)
        self._changed(description)

    def _changed(self, description: str = "") -> None:
        self.render_all()
        if description:
            self.log(description)
        self.saved_lbl.configure(text="Saving…")
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
        self._autosave_job = self.after(600, self._autosave)

    def _autosave(self) -> None:
        self._autosave_job = None
        if self.plan is None:
            return
        self.app.store.save_plan(self.plan.to_obj())
        self._saved_at = time.strftime("%H:%M")
        self.saved_lbl.configure(text=f"Saved {self._saved_at}")

    def undo(self) -> None:
        if self.plan is not None and self._undo:
            self._redo.append(self.plan.to_obj())
            self.plan = Plan.from_obj(self._undo.pop())
            self._changed("Undo")
            self.app.live.say("Undo")

    def redo(self) -> None:
        if self.plan is not None and self._redo:
            self._undo.append(self.plan.to_obj())
            self.plan = Plan.from_obj(self._redo.pop())
            self._changed("Redo")

    # -- node operations ---------------------------------------------------------

    def add_node(self, kind: str) -> None:
        n = len([t for t in (self.plan.tasks if self.plan else [])]) + 1
        while any(t.id == f"t{n}" for t in (self.plan.tasks if self.plan else [])):
            n += 1
        task_id = f"t{n}"
        title = {"gate": "Approval gate", "note": "Note"}.get(
            kind, f"Task {n}")

        def apply(plan: Plan):
            instructions = "" if kind in GATE_LIKE else (
                f"Complete {title.lower()} with sourced, verifiable results.")
            task = Task(id=task_id, type=kind, title=title,
                        instructions=instructions, needs=[])
            if kind == "gate" and self.selected:
                # A gate gates the next executable task: inserting it
                # before the selected task is what the user means.
                ids = [t.id for t in plan.tasks]
                at = ids.index(self.selected) if self.selected in ids \
                    else len(plan.tasks)
                plan.tasks.insert(at, task)
            else:
                plan.tasks.append(task)
            if kind == "gate":
                self._relink_gates(plan)

        self.mutate(f"Added {kind} {task_id}", apply)
        self.selected = task_id
        self.render_all()

    def delete_node(self, task_id: str) -> None:
        if self.plan is None:
            return
        task = next((t for t in self.plan.tasks if t.id == task_id), None)
        if task is None:
            return
        dependents = [t.id for t in self.plan.tasks if task_id in t.needs]
        if dependents:
            if not confirm(
                    self,
                    f"{', '.join(dependents)} depend on {task_id}. "
                    "Delete it anyway? Those dependencies are removed too.",
                    "Delete task", danger=True):
                return

        def apply(plan: Plan):
            plan.tasks = [t for t in plan.tasks if t.id != task_id]
            for t in plan.tasks:
                t.needs = [d for d in t.needs if d != task_id]
            for c in plan.constraints:
                if isinstance(c.scope, list) and task_id in c.scope:
                    c.scope = [a for a in c.scope if a != task_id]
            # A removed gate clears the flag it set on the next task.
            if task.type == "gate":
                for t in plan.tasks:
                    t.gate_before = False
                self._relink_gates(plan)

        self.mutate(f"Deleted {task_id}", apply)
        if self.selected == task_id:
            self.selected = None
        self.render_all()

    def move_task(self, task_id: str, delta: int) -> bool:
        """Alt+Up / Alt+Down (AC-11): swap with the neighbour unless a
        dependency would be broken; returns success for the status line."""
        if self.plan is None:
            return False
        tasks = self.plan.tasks
        i = next((k for k, t in enumerate(tasks) if t.id == task_id), None)
        if i is None:
            return False
        j = i + delta
        if not (0 <= j < len(tasks)):
            return False
        order = [t.id for t in tasks]
        order[i], order[j] = order[j], order[i]
        # Refuse a move that puts a task before one it depends on
        # (spec S4 canvas rule, also applied to list moves).
        pos = {tid: k for k, tid in enumerate(order)}
        for t in tasks:
            for dep in t.needs:
                if pos[dep] > pos[t.id]:
                    self.app.live.configure(
                        text=f"{t.id} needs {dep}, so it cannot run before it.")
                    return False

        def apply(plan: Plan):
            plan.tasks[i], plan.tasks[j] = plan.tasks[j], plan.tasks[i]
            if tasks[i].type == "gate" or tasks[j].type == "gate":
                self._relink_gates(plan)

        self.mutate(f"Moved {task_id}", apply)
        return True

    def add_dependency(self, task_id: str, onto: str) -> bool:
        if self.plan is None:
            return False
        if task_id == onto:
            return False
        tasks = {t.id: t for t in self.plan.tasks}
        if onto not in tasks or task_id not in tasks:
            return False
        pos = {t.id: k for k, t in enumerate(self.plan.tasks)}
        if pos[onto] < pos[task_id]:
            self.app.live.configure(
                text=f"{onto} comes earlier; data flows forward only.")
            return False
        if onto in tasks[task_id].needs:
            return False

        def apply(plan: Plan):
            # Data flows forward: the later task consumes the earlier
            # one's handoff (canvas port drag: out of task_id, into onto).
            t = next(t for t in plan.tasks if t.id == onto)
            if task_id not in t.needs:
                t.needs.append(task_id)

        self.mutate(f"Linked {task_id} → {onto}", apply)
        return True

    def _link_gate(self, plan: Plan, gate_id: str) -> None:
        """A gate node flags the next executable task (engine form)."""
        ids = [t.id for t in plan.tasks]
        try:
            i = ids.index(gate_id)
        except ValueError:
            return
        for t in plan.tasks[i + 1:]:
            if t.type in EXECUTABLE:
                t.gate_before = True
                break

    def _relink_gates(self, plan: Plan) -> None:
        for t in plan.tasks:
            if t.type in EXECUTABLE:
                t.gate_before = False
        pending = False
        for t in plan.tasks:
            if t.type == "gate":
                pending = True
            elif t.type in EXECUTABLE:
                if pending:
                    t.gate_before = True
                    pending = False

    # -- capability flags (shared with the engine's Rule of Two) --------------

    def flags_map(self) -> dict[str, tuple[bool, bool, bool]]:
        if self.plan is None:
            return {}
        flags: dict[str, tuple[bool, bool, bool]] = {}
        import tempfile
        with tempfile.TemporaryDirectory(prefix="las-ui-") as tmp:
            broker = PermissionBroker(Workspace(tmp))
            for t in self.plan.tasks:
                flags[t.id] = broker.compute_flags(t, self.plan, flags)
        return flags

    # -- preflight and run --------------------------------------------------------

    def validate_now(self) -> list:
        if self.plan is None:
            return []
        issues = validate_plan(self.plan)
        findings = lint(self.plan)
        self._render_lint(issues, findings)
        if not issues and not findings:
            self.app.live.configure(text="The plan validates cleanly")
        else:
            self.app.live.configure(
                text=f"{len(issues)} validation issues, "
                     f"{len(findings)} lint findings - see the Lint tab")
        return issues

    def run_plan(self) -> None:
        if self.plan is None:
            return
        issues = self.validate_now()
        errors = [i for i in issues]
        if errors:
            error_dialog(self, "PLAN-INVALID",
                         f"The plan has {len(errors)} problems.")
            self.dock.select(1)
            return
        findings = lint(self.plan)
        warnings = [f for f in findings if f.severity == "warning"]
        if warnings:
            listing = "\n".join(f"• {f.message}" for f in warnings)
            if not confirm(self,
                           "Run with these warnings?\n\n" + listing,
                           "Warnings"):
                return
        self._autosave()
        models = self.app.default_models()
        if not models:
            error_dialog(self, "UNREACHABLE",
                         "Add a provider first (Providers or the wizard).")
            return
        from las.modules.execution import DriverConfig
        caps = self.app.store.get_setting
        config = DriverConfig(
            failure_policy=caps("failure_policy") or "ask",
            cap_tokens_run=int(caps("cap_tokens_run") or 200000),
            cap_wall_minutes_run=float(caps("cap_wall_minutes_run") or 60),
            context_length=int(caps("context_length") or 8192),
            # "text" = the provider's models cannot call tools; the
            # worker then uses the text tool protocol instead.
            tool_calling=caps("tool_mode") != "text")
        run_id = self.app.engine.create_run(
            self.plan, models=models)
        self.run_id = run_id
        self.app.start_run(run_id, models, config)

    # -- plan menu -----------------------------------------------------------------

    def _plan_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0,
                       background=self.theme.c["surface"],
                       foreground=self.theme.c["text"],
                       activebackground=self.theme.c["accent"],
                       activeforeground=self.theme.c["accent_text"])
        menu.add_command(label="Save as template…",
                         command=self._save_template)
        menu.add_command(label="Export JSON…", command=self._export_json)
        menu.add_command(label="Version history…", command=self._versions)
        menu.add_command(label="Regenerate…", command=self._regenerate)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _save_template(self) -> None:
        if self.plan is None:
            return
        dlg = Dialog(self, "Save as template",
                     buttons=[("Save", "Primary.TButton", "save"),
                              ("Cancel", "", "close")])
        name_var = tk.StringVar(value=self.plan.title)
        desc_var = tk.StringVar(value="")
        ttk.Label(dlg.body, text="Name").grid(row=0, column=0, sticky="w",
                                              padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=name_var).grid(
            row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(dlg.body, text="Description").grid(
            row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=desc_var).grid(
            row=1, column=1, sticky="ew", padx=6, pady=4)

        def save():
            from las.modules.library import save_template
            try:
                save_template(self.app.store, self.plan.to_obj(),
                              name_var.get(), desc_var.get())
                self.app.live.configure(text=f"Template {name_var.get()} saved")
            except Exception as exc:                      # noqa: BLE001
                error_dialog(self, "PLAN-INVALID", str(exc))
        dlg.on_save = save
        dlg.wait_window()

    def _export_json(self) -> None:
        if self.plan is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile=f"{self.plan.title[:30].replace(' ', '-')}.json")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.plan.to_obj(), fh, indent=1, ensure_ascii=False)
        self.app.live.configure(text=f"Plan exported to {path}")

    def _versions(self) -> None:
        """Runs created from this plan (plan_version_id history)."""
        if self.plan is None:
            return
        rows = []
        for run_id in self.app.store.run_ids():
            first = self.app.store.first_event(run_id)
            if first and first.payload.get("plan_id") == self.plan.id:
                summary = self.app.engine.list_summaries(status="") or []
                row = next((r for r in summary if r["run_id"] == run_id), None)
                rows.append((run_id, row["status"] if row else "?",
                             (row.get("started") or "")[:19]))
        dlg = Dialog(self, "Version history",
                     buttons=[("Close", "", "close")])
        tree = ttk.Treeview(dlg.body, columns=("run", "status", "started"),
                            show="headings", height=8)
        for cid, head in [("run", "Run"), ("status", "Status"),
                          ("started", "Started")]:
            tree.heading(cid, text=head)
            tree.column(cid, width=150, anchor="w")
        for r in rows:
            tree.insert("", "end", values=r)
        tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _regenerate(self) -> None:
        """Re-plan the same goal (opens S3 prefilled)."""
        if self.plan is None or not self.plan.goal:
            return
        from las.app.gui.s3_goal import NewGoalScreen
        self.app.show("goal")
        screen = self.app._screens.get("goal")
        screen.goal.delete("1.0", "end")
        screen.goal.insert("1.0", self.plan.goal)
        screen._update_counter()

    # -- view control -----------------------------------------------------------

    def switch_view(self) -> None:
        self.view_mode = "list" if self.view_mode == "canvas" else "canvas"
        self.view_btn.configure(
            text="Canvas view" if self.view_mode == "list" else "List view")
        if self.view_mode == "list":
            self.canvas_view.pack_forget()
            self.list_view.pack(fill="both", expand=True)
            self.list_view.refresh()
        else:
            self.list_view.pack_forget()
            self.canvas_view.pack(fill="both", expand=True)
            self.canvas_view.render()
        self.app.live.say(
            "List view" if self.view_mode == "list" else "Canvas view")

    def _zoom(self, delta: float) -> None:
        self.zoom = max(0.5, min(1.6, self.zoom + delta))
        self.zoom_lbl.configure(text=f"{round(self.zoom * 100)}%")
        self.canvas_view.render()

    def _toggle_minimap(self) -> None:
        self.show_minimap = not self.show_minimap
        self.canvas_view.render()

    def auto_layout(self) -> None:
        self.canvas_view.render(relayout=True)

    # -- rendering --------------------------------------------------------------

    def render_all(self) -> None:
        if self.view_mode == "canvas":
            self.canvas_view.render()
        else:
            self.list_view.refresh()
        self.inspector.refresh()
        self._render_constraints()
        self.validate_now()

    def _render_constraints(self) -> None:
        self.cons_tree.delete(*self.cons_tree.get_children())
        if self.plan is None:
            return
        for c in self.plan.constraints:
            scope = c.scope if isinstance(c.scope, list) else None
            self.cons_tree.insert("", "end", values=(
                c.id, c.text, c.prerequisite or "", c.authority,
                c.fallback or "",
                ", ".join(scope) if scope else "all"))

    def _render_lint(self, issues, findings) -> None:
        self.lint_tree.delete(*self.lint_tree.get_children())
        for i in issues:
            self.lint_tree.insert("", "end", values=(
                i.rule, "error", i.message, i.task_id or ""))
        for f in findings:
            self.lint_tree.insert("", "end", values=(
                f.rule, f.severity, f.message, f.task_id or ""))

    def _focus_finding(self, _event) -> None:
        sel = self.lint_tree.selection()
        if sel:
            task_id = self.lint_tree.item(sel[0], "values")[3]
            if task_id:
                self.selected = task_id
                self.render_all()

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end",
                             time.strftime("%H:%M:%S ") + text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def log_run_event(self, text: str) -> None:
        self.log(text)
