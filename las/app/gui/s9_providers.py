"""S9 Providers (PRV-1 to PRV-4): profiles, tests, models.

Left: profiles with a health label (Connected, Key needed,
Unreachable).  Right: detail form, Test connection with the
capability table (reachable, authenticated, models listed, streaming,
tool calling, JSON schema, token counting, router mode), the models
table with Refresh, and Delete with confirmation.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import Dialog, confirm, error_dialog
from las.modules.providers import PRESETS, build_adapter, probe
from las.shared.provider_contract import ProviderProfile

HEALTH = {"connected": "● Connected", "key": "● Key needed",
          "unreachable": "● Unreachable", "unknown": "● Not checked"}


class ProvidersScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self._selected = None
        self._build()
        self._reload()

    # -- layout ----------------------------------------------------------------

    def _build(self) -> None:
        ttk.Label(self, text="Providers", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8))
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        # Left: profile list
        left = ttk.Frame(body)
        body.add(left, weight=0)
        self.listbox = tk.Listbox(left, width=28,
                                  background=self.theme.c["surface"],
                                  foreground=self.theme.c["text"],
                                  selectbackground=self.theme.c["select"],
                                  font=self.theme.font(), relief="solid",
                                  borderwidth=1, activestyle="dotbox")
        self.listbox.pack(fill="y", expand=True, padx=(0, 6))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._load_detail())
        ttk.Button(left, text="＋ Add (wizard)",
                   style="Primary.TButton",
                   command=self._add_wizard).pack(fill="x", pady=(6, 0))

        # Right: detail
        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.detail = ttk.Frame(right)
        self.detail.pack(fill="both", expand=True)

        d = self.detail
        self.name_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.kind_var = tk.StringVar(value="lmstudio")
        self.headers_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value="60")

        row = 0
        for label, var, w in [("Name", self.name_var, 24),
                              ("Base URL", self.url_var, 40),
                              ("Preset", self.kind_var, 18)]:
            ttk.Label(d, text=label).grid(row=row, column=0, sticky="w",
                                          padx=6, pady=3)
            if label == "Preset":
                box = ttk.Combobox(d, textvariable=var,
                                   values=sorted(PRESETS), state="readonly")
            else:
                box = ttk.Entry(d, textvariable=var, width=w)
            box.grid(row=row, column=1, sticky="w", padx=6, pady=3)
            row += 1
        ttk.Label(d, text="API key (masked)").grid(row=row, column=0,
                                                   sticky="w", padx=6, pady=3)
        self.key_lbl = ttk.Label(d, text="—", style="Muted.TLabel")
        self.key_lbl.grid(row=row, column=1, sticky="w", padx=6)
        ttk.Button(d, text="Replace key…", command=self._replace_key).grid(
            row=row, column=2, padx=6)
        row += 1
        ttk.Label(d, text="Extra headers (JSON)").grid(row=row, column=0,
                                                       sticky="w", padx=6,
                                                       pady=3)
        ttk.Entry(d, textvariable=self.headers_var,
                  width=40).grid(row=row, column=1, sticky="w", padx=6)
        row += 1
        ttk.Label(d, text="Timeout (seconds)").grid(row=row, column=0,
                                                    sticky="w", padx=6, pady=3)
        ttk.Entry(d, textvariable=self.timeout_var,
                  width=8).grid(row=row, column=1, sticky="w", padx=6)
        row += 1
        actions = ttk.Frame(d)
        actions.grid(row=row, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(actions, text="Save", style="Primary.TButton",
                   command=self._save).pack(side="left")
        ttk.Button(actions, text="Test connection",
                   command=self._test).pack(side="left", padx=6)
        ttk.Button(actions, text="Delete",
                   style="Danger.TButton",
                   command=self._delete).pack(side="left")

        # Test table
        ttk.Label(d, text="Connection test", style="Heading.TLabel").grid(
            row=row + 1, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self.test_tree = ttk.Treeview(d, columns=("check", "result", "why"),
                                      show="headings", height=8)
        for cid, head, w in [("check", "Check", 150),
                             ("result", "Result", 90),
                             ("why", "Explanation", 300)]:
            self.test_tree.heading(cid, text=head)
            self.test_tree.column(cid, width=w, anchor="w")
        self.test_tree.grid(row=row + 2, column=0, columnspan=3,
                            sticky="nsew", padx=6)
        d.rowconfigure(row + 2, weight=1)
        d.columnconfigure(1, weight=1)

        # Models table
        ttk.Label(d, text="Models", style="Heading.TLabel").grid(
            row=row + 3, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self.models_tree = ttk.Treeview(
            d, columns=("id", "loaded", "context", "capabilities", "roles"),
            show="headings", height=6)
        for cid, head, w in [("id", "ID", 200), ("loaded", "Loaded", 70),
                             ("context", "Context", 80),
                             ("capabilities", "Capabilities", 180),
                             ("roles", "Role tags", 110)]:
            self.models_tree.heading(cid, text=head)
            self.models_tree.column(cid, width=w, anchor="w")
        self.models_tree.grid(row=row + 4, column=0, columnspan=3,
                              sticky="nsew", padx=6, pady=(0, 6))
        ttk.Button(d, text="Refresh models",
                   command=self._refresh_models).grid(
            row=row + 5, column=0, sticky="w", pady=(0, 8))

    def focus_regions(self) -> list:
        return [self.listbox, self.detail]

    def on_show(self, **_ctx) -> None:
        self._reload()

    # -- data ------------------------------------------------------------------

    def _profiles(self) -> list[ProviderProfile]:
        out = []
        for p in self.app.store.list_providers():
            if isinstance(p, ProviderProfile):
                out.append(p)
            else:
                out.append(ProviderProfile.from_obj(p))
        return out

    def _reload(self) -> None:
        self.listbox.delete(0, "end")
        for p in self._profiles():
            self.listbox.insert("end", f"{p.name}  ({p.id})")
        if self._profiles():
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(0)
            self._load_detail()

    def _current(self) -> ProviderProfile | None:
        sel = self.listbox.curselection()
        profiles = self._profiles()
        if sel and 0 <= sel[0] < len(profiles):
            return profiles[sel[0]]
        return None

    def _load_detail(self) -> None:
        p = self._current()
        if p is None:
            return
        self._selected = p.id
        self.name_var.set(p.name)
        self.url_var.set(p.base_url)
        self.kind_var.set(p.kind)
        self.headers_var.set(str(p.extra_headers or {}))
        self.timeout_var.set(str(p.timeout_seconds))
        self.key_lbl.configure(
            text="set" if p.auth_env else "none (local server)")
        self.test_tree.delete(*self.test_tree.get_children())
        self.models_tree.delete(*self.models_tree.get_children())

    # -- actions ------------------------------------------------------------------

    def _add_wizard(self) -> None:
        from las.app.gui.s1_wizard import SetupWizard
        self.app.show("wizard")

    def _save(self) -> None:
        p = self._current()
        if p is None:
            return
        p.name = self.name_var.get().strip() or p.name
        p.base_url = self.url_var.get().strip()
        p.kind = self.kind_var.get()
        p.timeout_seconds = float(self.timeout_var.get() or 60)
        self.app.store.save_provider(p)
        self._reload()
        self.app.live.configure(text=f"Saved provider {p.name}")

    def _replace_key(self) -> None:
        p = self._current()
        if p is None:
            return
        dlg = Dialog(self, f"Replace key for {p.name}",
                     buttons=[("Save", "Primary.TButton", "save"),
                              ("Cancel", "", "close")])
        var = tk.StringVar()
        entry = ttk.Entry(dlg.body, textvariable=var, show="•")
        entry.pack(fill="x", padx=8, pady=8)
        entry.focus_set()
        ttk.Label(dlg.body, text="The key is stored by reference "
                                 "(environment name), never in events "
                                 "or plans.",
                  style="Muted.TLabel", wraplength=340).pack(padx=8)

        def save():
            value = var.get().strip()
            if value:
                import os
                env_name = f"LAS_KEY_{p.id.upper()}"
                os.environ[env_name] = value
                p.auth_env = f"env:{env_name}"
                self.app.store.save_provider(p)
                self._load_detail()
                self.app.live.configure(text="Key stored")
            dlg.close()
        dlg.on_save = save
        dlg.wait_window()

    def _test(self) -> None:
        p = self._current()
        if p is None:
            return
        self.test_tree.delete(*self.test_tree.get_children())
        self.test_tree.insert("", "end", values=(
            "reachable", "…", "testing"))
        try:
            adapter = build_adapter(p)
            try:
                models = adapter.list_models()
                model = models[0]["id"] if models else ""
            except Exception:                          # noqa: BLE001
                model = ""
            record = probe(adapter, model, provider_id=p.id)
        except Exception as exc:                      # noqa: BLE001
            self.test_tree.delete(*self.test_tree.get_children())
            self.test_tree.insert("", "end", values=(
                "reachable", "Fail", str(exc)))
            return
        self.test_tree.delete(*self.test_tree.get_children())
        def cell(ok, unknown=False):
            return "Unknown" if unknown else ("Pass" if ok else "Fail")
        rows = [
            ("reachable", cell(record.reachable),
             "" if record.reachable else f"Cannot reach {p.base_url}"),
            ("authenticated", cell(record.authenticated),
             "" if record.authenticated else
             "The server rejected this key. Check that it was copied "
             "completely."),
            ("models listed", cell(record.models_listed),
             f"{len(record.models or [])} models" if record.models_listed
             else "no models are loaded"),
            ("streaming", cell(record.streaming, not record.probed_at),
             "streaming probe"),
            ("tool calling", cell(record.tool_calling,
                                  not record.probed_at), "tool probe"),
            ("JSON-schema output", cell(record.json_schema,
                                        not record.probed_at), "probe"),
            ("token counting", cell(record.token_counting,
                                    not record.probed_at), "probe"),
            ("router mode", cell(record.router_mode,
                                 not record.probed_at),
             "yes" if record.router_mode else "no"),
        ]
        for row in rows:
            self.test_tree.insert("", "end", values=row)
        self._refresh_models()

    def _refresh_models(self) -> None:
        p = self._current()
        if p is None:
            return
        self.models_tree.delete(*self.models_tree.get_children())
        try:
            adapter = build_adapter(p)
            for m in adapter.list_models():
                caps = []
                if m.get("streaming"):
                    caps.append("streaming")
                if m.get("tool_calling"):
                    caps.append("tools")
                if m.get("json_schema"):
                    caps.append("json")
                ctx = m.get("context_length") or ""
                if ctx and int(ctx) < 8192:
                    caps.append("short context")
                roles = []
                mid = str(m.get("id", "")).lower()
                if any(k in mid for k in ("7b", "8b", "instruct", "chat")):
                    roles.append("worker")
                if any(k in mid for k in ("70b", "72b", "large")):
                    roles.append("planner")
                self.models_tree.insert("", "end", values=(
                    m.get("id", ""), "yes" if m.get("loaded", True) else "no",
                    ctx, ", ".join(caps) or "—", ", ".join(roles) or "worker"))
        except Exception as exc:                      # noqa: BLE001
            self.models_tree.insert("", "end", values=(
                f"(could not list models: {exc})", "", "", "", ""))

    def _delete(self) -> None:
        p = self._current()
        if p is None:
            return
        if not confirm(self, f"Delete provider {p.name}? Its key reference "
                             "is removed too.", "Delete provider",
                       danger=True):
            return
        # store-level delete: remove row via direct db (no API on Store)
        self.app.store.db.execute("DELETE FROM providers WHERE id=?",
                                  (p.id,))
        self.app.store.db.commit()
        self._reload()
        self.app.live.configure(text=f"Deleted provider {p.name}")
