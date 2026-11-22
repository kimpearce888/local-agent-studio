"""S2 Runs (OBS-1): the run history and entry point.

Filter bar (search text, status, model, date range), a sortable table
(title, status chip, tasks done/total, duration, models, tokens,
started), 50 rows per page, row actions (Open, Resume, Duplicate plan,
Export, Delete), multi-select bulk delete with a count dialog, and the
spec empty state.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import EmptyState, StatusChip, confirm

PAGE_SIZE = 50
COLUMNS = [
    # (id, heading, width, stretch)
    ("title", "Title", 260, True),
    ("status", "Status", 120, False),
    ("tasks", "Tasks", 90, False),
    ("duration", "Duration", 90, False),
    ("models", "Models", 150, False),
    ("tokens", "Tokens", 80, False),
    ("started", "Started", 150, False),
]
STATUS_ORDER = ["succeeded", "failed", "blocked", "running", "paused",
                "awaiting_approval", "cancelled", "queued"]


def _duration_text(seconds) -> str:
    if seconds in ("", None):
        return "—"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


class RunsScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self._rows: list[dict] = []
        self._filtered: list[dict] = []
        self._sort = ("started", True)      # (column, descending)
        self._page = 0
        self._build()
        self.refresh()

    # -- layout --------------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Runs", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="New goal", style="Primary.TButton",
                   command=self.app.open_new_goal).pack(side="right",
                                                        ipady=3)

        # Filter bar: search, status, model, date range (spec S2).
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filters())
        search = ttk.Entry(bar, textvariable=self.search_var, width=34)
        search.pack(side="left")
        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(
            side="left", padx=(4, 10))
        self.status_var = tk.StringVar(value="All statuses")
        status_box = ttk.Combobox(bar, textvariable=self.status_var,
                                  state="readonly", width=16,
                                  values=["All statuses"] + [
                                      s.replace("_", " ").title()
                                      for s in STATUS_ORDER])
        status_box.pack(side="left", padx=(0, 8))
        status_box.bind("<<ComboboxSelected>>",
                        lambda e: self._apply_filters())
        self.model_var = tk.StringVar(value="All models")
        self.model_box = ttk.Combobox(bar, textvariable=self.model_var,
                                      state="readonly", width=18,
                                      values=["All models"])
        self.model_box.pack(side="left", padx=(0, 8))
        self.model_box.bind("<<ComboboxSelected>>",
                            lambda e: self._apply_filters())
        self.date_var = tk.StringVar(value="All time")
        date_box = ttk.Combobox(bar, textvariable=self.date_var,
                                state="readonly", width=12,
                                values=["All time", "Today", "7 days",
                                        "30 days"])
        date_box.pack(side="left")
        date_box.bind("<<ComboboxSelected>>", lambda e: self._apply_filters())

        ttk.Button(bar, text="Delete selected", style="Danger.TButton",
                   command=self._bulk_delete).pack(side="right")

        # Table
        table_holder = ttk.Frame(self)
        table_holder.pack(fill="both", expand=True)
        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(table_holder, columns=cols, show="headings",
                                 selectmode="extended")
        for cid, heading, width, stretch in COLUMNS:
            self.tree.heading(cid, text=heading,
                              command=lambda c=cid: self._sort_by(c))
            self.tree.column(cid, width=width, stretch=stretch,
                             anchor="w")
        vsb = ttk.Scrollbar(table_holder, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self._open_selected())
        self.tree.bind("<Return>", lambda e: self._open_selected())
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        # Status chips need to sit inside the status column: draw them
        # via the icon+text pattern directly in the cell text.
        self.tree.tag_configure("succeeded", foreground=self.theme.c["success"])
        self.tree.tag_configure("failed", foreground=self.theme.c["danger"])
        self.tree.tag_configure("blocked", foreground=self.theme.c["warning"])
        self.tree.tag_configure("running", foreground=self.theme.c["info"])
        self.tree.tag_configure("paused", foreground=self.theme.c["warning"])
        self.tree.tag_configure("awaiting_approval",
                                foreground=self.theme.c["warning"])

        # Pager
        pager = ttk.Frame(self)
        pager.pack(fill="x", pady=6)
        self.page_label = ttk.Label(pager, text="", style="Muted.TLabel")
        self.page_label.pack(side="left")
        ttk.Button(pager, text="‹ Prev",
                   command=lambda: self._turn_page(-1)).pack(side="right")
        ttk.Button(pager, text="Next ›",
                   command=lambda: self._turn_page(1)).pack(side="right")

        # Row-action launcher (context menu + button bar)
        self.actions = tk.Menu(self, tearoff=0,
                               background=self.theme.c["surface"],
                               foreground=self.theme.c["text"],
                               activebackground=self.theme.c["accent"],
                               activeforeground=self.theme.c["accent_text"])
        for label, cmd in [("Open", self._open_selected),
                           ("Resume", self._resume_selected),
                           ("Duplicate plan", self._duplicate_selected),
                           ("Export…", self._export_selected),
                           ("Delete", self._delete_selected)]:
            self.actions.add_command(label=label, command=cmd)
        self.tree.bind("<Button-3>", self._popup_menu)

        self.empty = EmptyState(
            self, "No runs yet. Describe a goal to create your first plan.",
            "New goal", self.app.open_new_goal)

    def focus_regions(self) -> list:
        return [self.tree.master, self.tree]

    def on_show(self, **_ctx) -> None:
        self.refresh()

    # -- data ----------------------------------------------------------------

    def refresh(self) -> None:
        self._rows = self.app.engine.list_summaries()
        self._apply_filters()

    def _apply_filters(self) -> None:
        text = self.search_var.get().lower().strip()
        status = self.status_var.get()
        model = self.model_var.get()
        when = self.date_var.get()

        def keep(row) -> bool:
            if text and text not in row["title"].lower() \
                    and text not in row["run_id"].lower():
                return False
            if status != "All statuses" and \
                    row["status"].replace("_", " ").title() != status:
                return False
            if model != "All models" and model not in (
                    row.get("worker_model", ""), row.get("planner_model", "")):
                return False
            if when != "All time":
                from datetime import datetime, timedelta, timezone
                try:
                    started = datetime.fromisoformat(row.get("started", ""))
                except ValueError:
                    return False
                days = {"Today": 1, "7 days": 7, "30 days": 30}[when]
                if started.replace(tzinfo=None) < (
                        datetime.utcnow() - timedelta(days=days)):
                    return False
            return True

        self._filtered = [r for r in self._rows if keep(r)]
        models = sorted({r.get("worker_model", "") for r in self._rows
                         if r.get("worker_model")})
        self.model_box.configure(values=["All models"] + models)
        self._render()

    def _sort_by(self, column: str) -> None:
        """Click a heading: same column flips direction, new column
        starts ascending (title/model) or descending (the rest)."""
        if column == getattr(self, "_sort_col", None):
            self._sort_desc = not getattr(self, "_sort_desc", True)
        else:
            self._sort_desc = column in ("title", "models", "status")
        self._sort_col = column
        self._render()

    def _sort_key(self, row: dict):
        col = getattr(self, "_sort_col", "started")
        if col == "title":
            return row["title"].lower()
        if col == "status":
            s = row["status"]
            return (STATUS_ORDER.index(s) if s in STATUS_ORDER else 99, s)
        if col == "tasks":
            return (row["tasks_done"], row["tasks_total"])
        if col == "duration":
            try:
                return int(row.get("duration_s") or 0)
            except (TypeError, ValueError):
                return 0
        if col == "models":
            return f"{row.get('planner_model', '')}/{row.get('worker_model', '')}"
        if col == "tokens":
            return row.get("tokens", 0)
        return row.get("started", "")

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        rows = sorted(self._filtered,
                      key=self._sort_key,
                      reverse=getattr(self, "_sort_desc", True))
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page = min(self._page, total_pages - 1)
        start = self._page * PAGE_SIZE
        page_rows = rows[start:start + PAGE_SIZE]
        for row in page_rows:
            icon, text, _role = self.theme.status_appearance(row["status"])
            models = (f"{row.get('planner_model') or '—'} / "
                      f"{row.get('worker_model') or '—'}")
            self.tree.insert("", "end", iid=row["run_id"],
                             values=(row["title"][:60], f"{icon} {text}",
                                     f"{row['tasks_done']}/{row['tasks_total']}",
                                     _duration_text(row.get("duration_s")),
                                     models, row.get("tokens", 0),
                                     (row.get("started") or "")[:19].replace(
                                         "T", " ")),
                             tags=(row["status"],))
        if rows:
            self.page_label.configure(
                text=f"{len(rows)} runs · page {self._page + 1} of "
                     f"{total_pages}")
            self.empty.place_forget()
        else:
            self.page_label.configure(text="0 runs")
            if not self._rows:
                self.empty.place(relx=0.5, rely=0.5, relwidth=1.0,
                                 relheight=1.0)
            else:
                self.empty.place_forget()
                self.page_label.configure(text="No runs match the filters")

    def _turn_page(self, delta: int) -> None:
        total = max(1, (len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page = max(0, min(total - 1, self._page + delta))
        self._render()

    # -- actions ---------------------------------------------------------------

    def _popup_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.actions.tk_popup(event.x_root, event.y_root)

    def _selected(self) -> list[str]:
        return list(self.tree.selection())

    def _one(self) -> str | None:
        sel = self._selected()
        return sel[0] if len(sel) == 1 else None

    def _open_selected(self) -> None:
        run_id = self._one()
        if run_id:
            self.app.open_run(run_id)

    def _resume_selected(self) -> None:
        run_id = self._one()
        if not run_id:
            return
        state = self.app.engine.state(run_id)
        if state.status not in ("paused", "failed", "blocked", "awaiting_approval"):
            self.app.live.configure(text="Only paused, failed or blocked runs can resume")
            return
        self.app.open_run(run_id, resume=True)

    def _duplicate_selected(self) -> None:
        run_id = self._one()
        if not run_id:
            return
        first = self.app.store.first_event(run_id)
        plan_obj = first.payload.get("plan")
        if not plan_obj:
            return
        from las.shared.plan_contract import Plan
        plan = Plan.from_obj(plan_obj)
        plan.id = plan.id + "-copy"
        self.app.open_studio(plan)

    def _export_selected(self) -> None:
        run_id = self._one()
        if run_id:
            self.app.open_export(run_id)

    def _delete_selected(self) -> None:
        ids = self._selected()
        if not ids:
            return
        if not confirm(
                self,
                f"Delete {len(ids)} run{'s' if len(ids) > 1 else ''}? "
                "Their events and results are removed permanently.",
                "Delete runs", danger=True):
            return
        for run_id in ids:
            self.app.store.delete_run(run_id)
        self.app.live.configure(text=f"Deleted {len(ids)} run(s)")
        self.refresh()

    def _bulk_delete(self) -> None:
        self._delete_selected()
