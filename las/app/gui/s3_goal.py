"""S3 New goal (PLN-4, PLN-5): compose a goal and planning options.

Fields per the spec: goal (20 to 20,000 characters with a counter),
template picker with a variable form, granularity, maximum tasks,
stage barriers, critique pass, planner and worker model pickers with
inline capability warnings.  Planning runs off-thread with round
progress, streamed reply text and a Cancel button; on failure to
validate, the partial plan opens in S4 with the errors in the Lint tab.
"""

from __future__ import annotations

import re
import time
import tkinter as tk
from tkinter import ttk

from las.app.gui.runner import PlanningJob
from las.app.gui.widgets import Dialog
from las.shared.messages import message_with_fix
from las.shared.plan_contract import (
    Plan, plan_variables, substitute_variables,
)

GOAL_MIN, GOAL_MAX = 20, 20_000
GRANULARITY = [("coarse", "Coarse (about 3 to 6 tasks)"),
               ("normal", "Normal (about 6 to 15 tasks)"),
               ("fine", "Fine (about 15 to 30 tasks)")]


class NewGoalScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self.job: PlanningJob | None = None
        self._round = 0
        self._build()

    # -- layout --------------------------------------------------------------

    def _build(self) -> None:
        ttk.Label(self, text="New goal", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # Goal
        goal_box = ttk.Labelframe(body, text="Goal")
        goal_box.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        self.goal = tk.Text(goal_box, wrap="word", height=7,
                            background=self.theme.c["surface"],
                            foreground=self.theme.c["text"],
                            insertbackground=self.theme.c["text"],
                            relief="solid", borderwidth=1, padx=8, pady=6,
                            font=self.theme.font())
        self.goal.pack(fill="both", expand=True, padx=8, pady=8)
        self.counter = ttk.Label(goal_box, text="0 characters (20 minimum)",
                                 style="Muted.TLabel")
        self.counter.pack(anchor="e", padx=10, pady=(0, 6))
        self.goal.bind("<KeyRelease>", lambda e: self._update_counter())

        # Options
        opts = ttk.Labelframe(body, text="Planning options")
        opts.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for col in range(2):
            opts.columnconfigure(col, weight=1)

        ttk.Label(opts, text="Start from template").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.template_var = tk.StringVar(value="None")
        templates = ["None"] + [t["name"] for t in
                                self.app.store.list_templates()]
        self.template_box = ttk.Combobox(opts, textvariable=self.template_var,
                                         values=templates, state="readonly")
        self.template_box.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.template_box.bind("<<ComboboxSelected>>",
                               lambda e: self._use_template())

        ttk.Label(opts, text="Attachments (P1)").grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(opts, text="File and folder attachments arrive in a "
                             "later phase.", style="Muted.TLabel").grid(
            row=1, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(opts, text="Granularity").grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        self.granularity_var = tk.StringVar(value=GRANULARITY[1][1])
        g = ttk.Combobox(opts, textvariable=self.granularity_var,
                         values=[label for _k, label in GRANULARITY],
                         state="readonly")
        g.grid(row=2, column=1, sticky="ew", padx=8, pady=4)

        ttk.Label(opts, text="Maximum tasks").grid(
            row=3, column=0, sticky="w", padx=8, pady=4)
        self.max_tasks_var = tk.IntVar(value=30)
        ttk.Spinbox(opts, from_=1, to=100, textvariable=self.max_tasks_var,
                    width=6).grid(row=3, column=1, sticky="w", padx=8, pady=4)

        self.barriers_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Stage barriers "
                                   "(a collector after each stage)",
                        variable=self.barriers_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.critique_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Critique pass "
                                   "(a second planner session reviews the plan)",
                        variable=self.critique_var).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=2)

        # Models
        models = ttk.Labelframe(body, text="Models")
        models.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        models.columnconfigure(1, weight=1)
        models.columnconfigure(3, weight=1)
        ttk.Label(models, text="Planner model").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.planner_model_var = tk.StringVar()
        self.planner_model_box = ttk.Combobox(
            models, textvariable=self.planner_model_var)
        self.planner_model_box.grid(row=0, column=1, sticky="ew",
                                    padx=8, pady=4)
        ttk.Label(models, text="Worker model").grid(
            row=0, column=2, sticky="w", padx=8, pady=4)
        self.worker_model_var = tk.StringVar()
        self.worker_model_box = ttk.Combobox(
            models, textvariable=self.worker_model_var)
        self.worker_model_box.grid(row=0, column=3, sticky="ew",
                                   padx=8, pady=4)
        self.model_warning = ttk.Label(models, text="", style="Muted.TLabel",
                                       wraplength=520)
        self.model_warning.grid(row=1, column=0, columnspan=4,
                                sticky="w", padx=8, pady=(0, 4))
        self.planner_model_box.bind(
            "<<ComboboxSelected>>", lambda e: self._warn_models())
        self.worker_model_box.bind(
            "<<ComboboxSelected>>", lambda e: self._warn_models())
        ttk.Button(models, text="Reload model list",
                   command=self._load_models).grid(
            row=1, column=3, sticky="e", padx=8, pady=(0, 4))

        # Actions
        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, sticky="ew")
        self.plan_btn = ttk.Button(actions, text="Plan", style="Primary.TButton",
                                   command=self._start_planning)
        self.plan_btn.pack(side="right", ipadx=10, ipady=3)
        self.blank_btn = ttk.Button(
            actions, text="Start with a blank plan",
            command=self._blank_plan)
        self.blank_btn.pack(side="right", padx=8)

        # Progress area (spec: `Planning...`, streamed text, Cancel)
        self.progress = ttk.Frame(body)
        self.progress_lbl = ttk.Label(self.progress, text="Planning…")
        self.progress_lbl.pack(anchor="w", pady=(4, 2))
        self.progress_text = tk.Text(self.progress, wrap="word", height=5,
                                     background=self.theme.c["surface"],
                                     foreground=self.theme.c["text"],
                                     relief="solid", borderwidth=1,
                                     state="disabled", padx=8, pady=4,
                                     font=self.theme.font(-1))
        self.progress_text.pack(fill="x")
        self.cancel_btn = ttk.Button(self.progress, text="Cancel",
                                     command=self._cancel_planning)
        self.cancel_btn.pack(anchor="e", pady=4)
        self._load_models()

    def focus_regions(self) -> list:
        return [self.goal, self.goal.master]

    def on_show(self, **_ctx) -> None:
        pass

    # -- fields ----------------------------------------------------------------

    def _update_counter(self) -> None:
        n = len(self.goal.get("1.0", "end-1c"))
        if n < GOAL_MIN:
            self.counter.configure(
                text=f"{n} characters ({GOAL_MIN - n} more needed)")
        else:
            self.counter.configure(
                text=f"{n} characters ({GOAL_MAX - n} left)")

    def _load_models(self) -> None:
        """Model list from the configured server (the only outbound
        connection the app makes, spec 12)."""
        registry = self.app._provider_registry()
        ids = [p.get("id", p.id) if isinstance(p, dict) else p.id
               for p in self.app.store.list_providers()]
        models: list[str] = []
        for pid in ids:
            try:
                models += [m["id"] for m in registry.get(pid).list_models()]
            except Exception:                          # noqa: BLE001
                continue
        values = sorted(set(models)) or ["(no models listed - start a server)"]
        self.planner_model_box.configure(values=values)
        self.worker_model_box.configure(values=values)
        if not self.planner_model_var.get() and len(models) == 1:
            self.planner_model_var.set(models[0])
            self.worker_model_var.set(models[0])
        self._warn_models()

    def _warn_models(self) -> None:
        """Inline capability warnings (spec S1 step 4 rules, reused)."""
        warns = []
        for var, role in ((self.planner_model_var, "planner"),
                          (self.worker_model_var, "worker")):
            mid = var.get()
            if not mid or mid.startswith("("):
                continue
            if re.search(r"[-_/]?([0-9]+)(\.[0-9]+)?b\b", mid.lower()):
                size = float(re.search(
                    r"([0-9]+(?:\.[0-9]+)?)b", mid.lower()).group(1))
                if size < 7 and role == "planner":
                    warns.append("Small models may need several repair "
                                 "rounds when planning.")
        if warns:
            self.model_warning.configure(text="⚠ " + " ".join(warns))
        else:
            self.model_warning.configure(text="")

    def _use_template(self) -> None:
        name = self.template_var.get()
        if name == "None":
            return
        entry = next((t for t in self.app.store.list_templates()
                      if t["name"] == name), None)
        if not entry:
            return
        variables = plan_variables(entry["plan"])
        if variables:
            dlg = Dialog(self, f"Template variables: {name}",
                         buttons=[("Fill in", "Primary.TButton", "ok"),
                                  ("Cancel", "", "close")])
            fields = {}
            for i, v in enumerate(sorted(variables)):
                ttk.Label(dlg.body, text=v).grid(row=i, column=0,
                                                 sticky="w", padx=8, pady=4)
                var = tk.StringVar()
                ttk.Entry(dlg.body, textvariable=var).grid(
                    row=i, column=1, sticky="ew", padx=8, pady=4)
                fields[v] = var
            dlg.on_ok = lambda: self._fill_template(entry, fields)
            dlg.wait_window()
        else:
            self._fill_template(entry, {})

    def _fill_template(self, entry, fields) -> None:
        values = {v: var.get() for v, var in fields.items()}
        plan_obj = substitute_variables(entry["plan"], values)
        self.goal.delete("1.0", "end")
        self.goal.insert("1.0", plan_obj.get("goal", ""))
        self._update_counter()
        self.app.live.configure(text=f"Template {entry['name']} loaded - "
                                     "edit the goal and press Plan")

    # -- planning --------------------------------------------------------------

    def _blank_plan(self) -> None:
        plan = Plan(id="plan-blank", title="Blank plan", goal="",
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    tasks=[])
        self.app.open_studio(plan)
        self.app.live.configure(text="Blank plan opened - add your first task")

    def _start_planning(self) -> None:
        goal = self.goal.get("1.0", "end-1c").strip()
        if not (GOAL_MIN <= len(goal) <= GOAL_MAX):
            self.app.live.configure(
                text=f"The goal needs {GOAL_MIN} to {GOAL_MAX} characters "
                     f"(now {len(goal)}).")
            return
        models_map = self.app.default_models()
        if not models_map:
            from las.app.gui.widgets import error_dialog
            error_dialog(self, "UNREACHABLE",
                         "Add a provider first in Providers or the setup "
                         "wizard.")
            return
        provider_id = models_map["default"][0]
        registry = self.app._provider_registry()
        try:
            adapter = registry.get(provider_id)
        except KeyError:
            from las.app.gui.widgets import error_dialog
            error_dialog(self, "UNREACHABLE",
                         f"Provider {provider_id} is not available.")
            return
        planner_model = self.planner_model_var.get()
        worker_model = self.worker_model_var.get()
        model = planner_model or models_map["default"][1]
        options = {
            "granularity": next(k for k, label in GRANULARITY
                                if label == self.granularity_var.get()),
            "max_tasks": int(self.max_tasks_var.get()),
            "stage_barriers": bool(self.barriers_var.get()),
        }
        self.plan_btn.configure(state="disabled")
        self.blank_btn.configure(state="disabled")
        self._round = 0
        self.progress.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self._set_progress_text("Planning…\n")
        self.job = PlanningJob(adapter, model, goal, options)
        self.job.start()
        self._pending_worker = worker_model
        self._poll_job()

    def _cancel_planning(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self._set_progress_text("Cancelling after this round…\n")

    def _set_progress_text(self, text: str) -> None:
        self.progress_text.configure(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.insert("1.0", text)
        self.progress_text.configure(state="disabled")

    def _poll_job(self) -> None:
        if self.job is None:
            return
        try:
            while True:
                kind, payload = self.job.events.get_nowait()
                if kind == "reply":
                    self._round += 1
                    label = ("Planning…" if self._round == 1 else
                             f"Checking the plan (repair attempt "
                             f"{self._round - 1} of 3)")
                    self.progress_lbl.configure(text=label)
                    self._set_progress_text(
                        f"{label}\n\n{payload[:4000]}")
                elif kind == "done":
                    self._finish_planning(payload)
                    return
                elif kind == "error":
                    self._plan_error(str(payload))
                    return
        except Exception:                              # noqa: BLE001
            pass
        if self.job.is_alive():
            self.after(120, self._poll_job)

    def _finish_planning(self, result) -> None:
        self.progress.grid_forget()
        self.plan_btn.configure(state="normal")
        self.blank_btn.configure(state="normal")
        self.job = None
        if result.cancelled:
            self.app.live.configure(text="Planning cancelled")
            return
        if result.error:
            msg, fix = message_with_fix(result.error)
            self._set_progress_text(f"{result.error}: {msg}\nFix: {fix}")
            self.progress.grid(row=4, column=0, sticky="ew")
            return
        plan = result.plan
        if not result.valid:
            # Spec S3: open the partial plan with errors in the Lint tab.
            self.app.open_studio(plan, lint_on_open=True)
            self.app.live.configure(
                text="The planner could not produce a valid plan - "
                     "fix the errors in the Lint tab")
            return
        self.app.open_studio(plan)
        self.app.live.configure(
            text=f"Plan ready: {len(plan.tasks)} tasks, "
                 f"{len(plan.constraints)} constraints extracted")

    def _plan_error(self, detail: str) -> None:
        self.plan_btn.configure(state="normal")
        self.blank_btn.configure(state="normal")
        self.job = None
        self._set_progress_text(f"Planning failed: {detail}")
        self.progress.grid(row=4, column=0, sticky="ew")
