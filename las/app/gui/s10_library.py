"""S10 Library (LIB-1, LIB-2): templates and role profiles.

Two tabs.  Templates: cards with name, description, task count and
variables; Use, Duplicate, Edit, Export, Delete; built-ins cannot be
deleted but can be duplicated.  Role profiles: a form for name, system
prompt, parameters, tool allowlist and default model.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk, filedialog

from las.app.gui.widgets import Dialog, confirm
from las.modules.library import save_template
from las.shared.plan_contract import Plan, plan_variables


class LibraryScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self._build()
        self.reload()

    def _build(self) -> None:
        ttk.Label(self, text="Library", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)

        # Templates
        tpl = ttk.Frame(self.tabs)
        self.tabs.add(tpl, text="Templates")
        bar = ttk.Frame(tpl)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="New template from Studio",
                   command=self._note_no_plan).pack(side="left")
        self.tpl_tree = ttk.Treeview(
            tpl, columns=("name", "description", "tasks", "variables"),
            show="headings")
        for cid, head, w in [("name", "Name", 160),
                             ("description", "Description", 300),
                             ("tasks", "Tasks", 60),
                             ("variables", "Variables", 120)]:
            self.tpl_tree.heading(cid, text=head)
            self.tpl_tree.column(cid, width=w, anchor="w")
        self.tpl_tree.pack(fill="both", expand=True, padx=4, pady=4)
        acts = ttk.Frame(tpl)
        acts.pack(fill="x", pady=4)
        ttk.Button(acts, text="Use", style="Primary.TButton",
                   command=self._use).pack(side="left")
        ttk.Button(acts, text="Duplicate",
                   command=self._duplicate).pack(side="left", padx=6)
        ttk.Button(acts, text="Edit",
                   command=self._edit).pack(side="left")
        ttk.Button(acts, text="Export…",
                   command=self._export).pack(side="left", padx=6)
        ttk.Button(acts, text="Delete", style="Danger.TButton",
                   command=self._delete).pack(side="left")

        # Role profiles
        roles = ttk.Frame(self.tabs)
        self.tabs.add(roles, text="Role profiles")
        rbar = ttk.Frame(roles)
        rbar.pack(fill="x", pady=4)
        ttk.Button(rbar, text="New role profile",
                   command=lambda: self._edit_role(None)).pack(side="left")
        self.role_tree = ttk.Treeview(roles, columns=("name", "model"),
                                      show="headings")
        self.role_tree.heading("name", text="Name")
        self.role_tree.heading("model", text="Default model")
        self.role_tree.column("name", width=220, anchor="w")
        self.role_tree.column("model", width=200, anchor="w")
        self.role_tree.pack(fill="both", expand=True, padx=4, pady=4)
        ttk.Button(roles, text="Edit selected",
                   command=lambda: self._edit_role(
                       self.role_tree.selection())).pack(pady=4)

    def focus_regions(self) -> list:
        return [self.tabs]

    def on_show(self, **_ctx) -> None:
        self.reload()

    def reload(self) -> None:
        self.tpl_tree.delete(*self.tpl_tree.get_children())
        for t in self.app.store.list_templates():
            plan_obj = t.get("plan") or {}
            self.tpl_tree.insert("", "end", iid=t["id"], values=(
                ("★ " if t.get("builtin") else "") + t["name"],
                t.get("description", ""),
                t.get("task_count", len(plan_obj.get("tasks", []))),
                ", ".join(sorted(plan_variables(plan_obj))) or "—"))
        self.role_tree.delete(*self.role_tree.get_children())
        for r in self.app.store.list_role_profiles():
            self.role_tree.insert("", "end", iid=r.get("id", r.get("name")),
                                  values=(r.get("name", ""),
                                          r.get("default_model", "—")))

    # -- template actions ---------------------------------------------------------

    def _selected_template(self) -> dict | None:
        sel = self.tpl_tree.selection()
        if not sel:
            return None
        tid = sel[0]
        return next((t for t in self.app.store.list_templates()
                     if t["id"] == tid), None)

    def _note_no_plan(self) -> None:
        self.app.live.configure(
            text="Save the current plan as a template from the Studio's "
                 "Plan menu")

    def _use(self) -> None:
        t = self._selected_template()
        if t is None:
            return
        plan = Plan.from_obj(t["plan"])
        self.app.open_studio(plan)
        self.app.live.configure(text=f"Template {t['name']} opened in Studio")

    def _duplicate(self) -> None:
        t = self._selected_template()
        if t is None:
            return
        save_template(self.app.store, t["plan"],
                      f"{t['name']} (copy)",
                      t.get("description", ""), builtin=False)
        self.reload()
        self.app.live.configure(text=f"Duplicated {t['name']}")

    def _edit(self) -> None:
        t = self._selected_template()
        if t is None:
            return
        plan = Plan.from_obj(t["plan"])
        self.app.open_studio(plan)
        self.app.live.configure(
            text="Editing a copy - Save as template from the Plan menu "
                 "when done")

    def _export(self) -> None:
        t = self._selected_template()
        if t is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile=f"{t['name']}.json")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(t, fh, indent=1, ensure_ascii=False)
        self.app.live.configure(text=f"Template exported to {path}")

    def _delete(self) -> None:
        t = self._selected_template()
        if t is None:
            return
        if t.get("builtin"):
            self.app.live.configure(
                text="Built-in templates cannot be deleted; they can be "
                     "duplicated")
            return
        if not confirm(self, f"Delete template {t['name']}?",
                       "Delete template", danger=True):
            return
        self.app.store.db.execute("DELETE FROM templates WHERE id=?",
                                  (t["id"],))
        self.app.store.db.commit()
        self.reload()

    # -- role profiles ------------------------------------------------------------

    def _edit_role(self, selection) -> None:
        existing = None
        if selection:
            rid = selection[0]
            existing = next((r for r in self.app.store.list_role_profiles()
                             if r.get("id") == rid or r.get("name") == rid),
                            None)
        dlg = Dialog(self, "Role profile",
                     buttons=[("Save", "Primary.TButton", "save"),
                              ("Cancel", "", "close")])
        name = tk.StringVar(value=(existing or {}).get("name", ""))
        prompt = tk.StringVar(value=(existing or {}).get("system_prompt", ""))
        model = tk.StringVar(value=(existing or {}).get("default_model", ""))
        tools = tk.StringVar(value=", ".join(
            (existing or {}).get("tool_allowlist", [])))
        ttk.Label(dlg.body, text="Name").grid(row=0, column=0, sticky="w",
                                              padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=name).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Label(dlg.body, text="System prompt").grid(
            row=1, column=0, sticky="nw", padx=6, pady=4)
        pt = tk.Text(dlg.body, wrap="word", height=4,
                     background=dlg._c["surface"],
                     foreground=dlg._c["text"], relief="solid",
                     borderwidth=1)
        pt.grid(row=1, column=1, sticky="ew", padx=6)
        pt.insert("1.0", prompt.get())
        ttk.Label(dlg.body, text="Default model").grid(
            row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=model).grid(
            row=2, column=1, sticky="ew", padx=6)
        ttk.Label(dlg.body, text="Tool allowlist (comma separated)").grid(
            row=3, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=tools).grid(
            row=3, column=1, sticky="ew", padx=6)

        def save():
            if not name.get().strip():
                return
            profile = {
                "id": (existing or {}).get("id")
                    or f"role-{name.get().strip().lower().replace(' ', '-')}",
                "name": name.get().strip(),
                "system_prompt": pt.get("1.0", "end-1c"),
                "default_model": model.get().strip(),
                "tool_allowlist": [t.strip() for t in tools.get().split(",")
                                   if t.strip()],
            }
            self.app.store.save_role_profile(profile)
            self.reload()
            dlg.close()
        dlg.on_save = save
        dlg.wait_window()
