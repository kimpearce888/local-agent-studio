"""S11 Settings (SET-1 to SET-4): seven sections.

General (theme, language, shortcut editor), Runs (default caps and
policies), Data (location, retention, export/delete), Privacy
(telemetry, network log), Updates, Accessibility (reduce motion, high
contrast, text size, default list view), Advanced (log level,
diagnostics, engine details).
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk

from las.app.gui.app import DEFAULT_SHORTCUTS, SHORTCUT_HELP
from las.app.gui.widgets import Dialog, confirm, info_dialog

RETENTION = ["keep forever", "90 days", "30 days"]


class SettingsScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        ttk.Label(self, text="Settings", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self._build_general()
        self._build_runs()
        self._build_data()
        self._build_privacy()
        self._build_updates()
        self._build_accessibility()
        self._build_advanced()

    def focus_regions(self) -> list:
        return [self.tabs]

    def _set(self, key, value) -> None:
        self.app.store.set_setting(key, value)

    def _get(self, key, default=""):
        try:
            return self.app.store.get_setting(key) or default
        except Exception:                              # noqa: BLE001
            return default

    # -- General ---------------------------------------------------------------

    def _build_general(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="General")
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="Theme").pack(side="left")
        theme_var = tk.StringVar(value=self._get("theme", "light"))
        box = ttk.Combobox(row, textvariable=theme_var, state="readonly",
                           values=["light", "dark", "system",
                                   "high-contrast"])
        box.pack(side="left", padx=8)
        theme_var.trace_add("write", lambda *_: self._apply_theme(
            theme_var.get()))

        row2 = ttk.Frame(tab)
        row2.pack(fill="x", padx=10, pady=4)
        ttk.Label(row2, text="Language").pack(side="left")
        lang = tk.StringVar(value="English")
        ttk.Combobox(row2, textvariable=lang, state="readonly",
                     values=["English"]).pack(side="left", padx=8)

        ttk.Button(tab, text="Shortcut editor…",
                   command=self._shortcut_editor).pack(anchor="w",
                                                       padx=10, pady=8)

    def _apply_theme(self, mode: str) -> None:
        if mode == "system":
            # Tk exposes no OS-preference API; 'system' follows the
            # LAS_THEME environment variable and defaults to light.
            mode = os.environ.get("LAS_THEME", "light")
        self._set("theme", mode)
        self.app.theme.set_mode(mode)

    def _shortcut_editor(self) -> None:
        """All shortcuts are editable (spec 9)."""
        dlg = Dialog(self, "Shortcut editor",
                     buttons=[("Save", "Primary.TButton", "save"),
                              ("Reset to defaults", "", "reset"),
                              ("Close", "", "close")])
        overrides = self._get("shortcuts", {}) or {}
        vars_ = {}
        for i, (label, action) in enumerate(SHORTCUT_HELP):
            ttk.Label(dlg.body, text=label).grid(row=i, column=0,
                                                 sticky="w", padx=6, pady=2)
            var = tk.StringVar(
                value=overrides.get(action, DEFAULT_SHORTCUTS[action]))
            ttk.Entry(dlg.body, textvariable=var, width=22).grid(
                row=i, column=1, sticky="w", padx=6, pady=2)
            vars_[action] = var

        def save():
            new = {a: v.get() for a, v in vars_.items()
                   if v.get().strip()}
            self._set("shortcuts", new)
            self.app.live.configure(
                text="Shortcuts saved - restart the app to apply them")
            dlg.close()

        def reset():
            self._set("shortcuts", {})
            self.app.live.configure(text="Shortcuts reset to defaults")
            dlg.close()

        dlg.on_save = save
        dlg.on_reset = reset
        dlg.wait_window()

    # -- Runs --------------------------------------------------------------------

    def _build_runs(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Runs")
        fields = [
            ("Token cap per run", "cap_tokens_run", "200000"),
            ("Wall-time cap (minutes)", "cap_wall_minutes_run", "60"),
            ("Token cap per task", "cap_tokens_task", "60000"),
            ("Context length", "context_length", "8192"),
        ]
        for i, (label, key, default) in enumerate(fields):
            ttk.Label(tab, text=label).grid(row=i, column=0, sticky="w",
                                            padx=10, pady=4)
            var = tk.StringVar(value=str(self._get(key, default)))
            ttk.Entry(tab, textvariable=var, width=12).grid(
                row=i, column=1, sticky="w", padx=6)
            var.trace_add("write", lambda *_k, k=key, v=var:
                          self._set(k, v.get()))
        i = len(fields)
        ttk.Label(tab, text="Failure policy").grid(row=i, column=0,
                                                   sticky="w", padx=10,
                                                   pady=4)
        policy = tk.StringVar(value=self._get("failure_policy", "ask"))
        ttk.Combobox(tab, textvariable=policy, state="readonly",
                     values=["ask", "stop", "skip"]).grid(
            row=i, column=1, sticky="w", padx=6)
        policy.trace_add("write", lambda *_: self._set(
            "failure_policy", policy.get()))
        ttk.Label(tab, text="Tool calls").grid(
            row=i + 1, column=0, sticky="w", padx=10, pady=4)
        mode = tk.StringVar(value=self._get("tool_mode", "native"))
        ttk.Combobox(tab, textvariable=mode, state="readonly",
                     values=["native", "text"]).grid(
            row=i + 1, column=1, sticky="w", padx=6)
        mode.trace_add("write", lambda *_: self._set(
            "tool_mode", mode.get()))
        ttk.Label(tab, text="Use 'text' when the provider's models "
                           "cannot call tools (Providers screen shows "
                           "no tools chip).",
                  style="Muted.TLabel").grid(
            row=i + 2, column=0, columnspan=2, sticky="w",
                    padx=10, pady=4)
        ttk.Label(tab, text="Queued runs: one at a time (fixed in 1.0)",
                  style="Muted.TLabel").grid(row=i + 3, column=0,
                                             columnspan=2, sticky="w",
                                             padx=10, pady=4)

    # -- Data ---------------------------------------------------------------------

    def _build_data(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Data")
        ttk.Label(tab, text=f"Location: {self.app.data}",
                  wraplength=520).pack(anchor="w", padx=10, pady=8)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Label(row, text="Retention").pack(side="left")
        var = tk.StringVar(value=self._get("retention", RETENTION[0]))
        ttk.Combobox(row, textvariable=var, state="readonly",
                     values=RETENTION).pack(side="left", padx=8)
        var.trace_add("write", lambda *_: self._set("retention", var.get()))
        acts = ttk.Frame(tab)
        acts.pack(anchor="w", padx=10, pady=10)
        ttk.Button(acts, text="Export all data…",
                   command=self._export_all).pack(side="left")
        ttk.Button(acts, text="Delete all data…", style="Danger.TButton",
                   command=self._delete_all).pack(side="left", padx=8)

    def _export_all(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile="las-all-data.json")
        if not path:
            return
        bundle = {"runs": []}
        for run_id in self.app.store.run_ids():
            events = [e.__dict__ if hasattr(e, "__dict__") else str(e)
                      for e in self.app.store.events(run_id)]
            bundle["runs"].append({"run_id": run_id, "events": events})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=1, ensure_ascii=False,
                      default=str)
        self.app.live.configure(text=f"All data exported to {path}")

    def _delete_all(self) -> None:
        if not confirm(self, "Delete every run, plan and template? "
                             "This cannot be undone.", "Delete all data",
                       danger=True):
            return
        for run_id in self.app.store.run_ids():
            self.app.store.delete_run(run_id)
        self.app.live.configure(text="All runs deleted")
        for screen in self.app._screens.values():
            if hasattr(screen, "refresh"):
                try:
                    screen.refresh()
                except Exception:                      # noqa: BLE001
                    pass

    # -- Privacy ---------------------------------------------------------------------

    def _build_privacy(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Privacy")
        ttk.Label(tab, text="Telemetry: off (fixed in 1.0)",
                  wraplength=520).pack(anchor="w", padx=10, pady=6)
        ttk.Label(tab, text="Crash reports: opt-in (P1)",
                  wraplength=520).pack(anchor="w", padx=10, pady=2)
        ttk.Label(tab, text="Network log - every connection the app made:",
                  wraplength=520).pack(anchor="w", padx=10, pady=(10, 4))
        log = tk.Text(tab, wrap="word", height=8,
                      background=self.theme.c["surface"],
                      foreground=self.theme.c["text"], relief="solid",
                      borderwidth=1,
                      font=self.theme.font(-1))
        log.pack(fill="both", expand=True, padx=10, pady=4)
        from las.app.gui.netlog import recent
        entries = recent(self.app.store)
        if entries:
            for e in entries[-50:]:
                log.insert("end", f"{e.get('ts', '')}  {e.get('action', '')} "
                                  f"{e.get('url', '')}  "
                                  f"{'ok' if e.get('ok') else 'failed'} "
                                  f"{e.get('detail', '')}\n")
        else:
            log.insert("end", "No connections yet.  With default settings "
                             "the app only contacts the servers you "
                             "configure.")
        log.configure(state="disabled")

    # -- Updates -----------------------------------------------------------------------

    def _build_updates(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Updates")
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="Channel").pack(side="left")
        var = tk.StringVar(value=self._get("update_channel", "stable"))
        ttk.Combobox(row, textvariable=var, state="readonly",
                     values=["stable", "beta"]).pack(side="left", padx=8)
        var.trace_add("write", lambda *_: self._set("update_channel",
                                                    var.get()))
        auto = tk.BooleanVar(value=self._get("auto_updates", "off") == "on")
        ttk.Checkbutton(tab, text="Automatic updates (off by default)",
                        variable=auto,
                        command=lambda: self._set(
                            "auto_updates", "on" if auto.get() else "off")
                        ).pack(anchor="w", padx=10, pady=4)
        ttk.Label(tab, text="Checking for updates requires a release "
                            "channel and signed manifests (DST-1); the "
                            "GUI refuses unsigned or older updates.",
                  wraplength=520, style="Muted.TLabel").pack(
            anchor="w", padx=10, pady=6)

    # -- Accessibility ---------------------------------------------------------------------

    def _build_accessibility(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Accessibility")
        motion = tk.BooleanVar(value=self._get("reduce_motion", False))
        ttk.Checkbutton(tab, text="Reduce motion",
                        variable=motion,
                        command=lambda: self._set("reduce_motion",
                                                  motion.get())
                        ).pack(anchor="w", padx=10, pady=6)
        contrast = tk.BooleanVar(value=self._get("theme", "") ==
                                 "high-contrast")
        ttk.Checkbutton(tab, text="High contrast",
                        variable=contrast,
                        command=lambda: self._set(
                            "theme",
                            "high-contrast" if contrast.get() else "light")
                        ).pack(anchor="w", padx=10, pady=2)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="Text size").pack(side="left")
        scale = tk.IntVar(value=int(self._get("text_scale", 100)))
        ttk.Spinbox(row, from_=100, to=200, increment=10,
                    textvariable=scale, width=6).pack(side="left", padx=8)
        ttk.Button(row, text="Apply",
                   command=lambda: self.app.theme.set_text_scale(
                       scale.get())).pack(side="left")
        list_default = tk.BooleanVar(
            value=self._get("use_list_view_default", False))
        ttk.Checkbutton(
            tab, text="Use List view by default (the accessible editor)",
            variable=list_default,
            command=lambda: self._set("use_list_view_default",
                                      list_default.get())).pack(
            anchor="w", padx=10, pady=6)

    # -- Advanced ------------------------------------------------------------------------------

    def _build_advanced(self) -> None:
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Advanced")
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="Log level").pack(side="left")
        var = tk.StringVar(value=self._get("log_level", "info"))
        ttk.Combobox(row, textvariable=var, state="readonly",
                     values=["debug", "info", "warning",
                             "error"]).pack(side="left", padx=8)
        var.trace_add("write", lambda *_: self._set("log_level", var.get()))
        ttk.Button(tab, text="Diagnostics bundle…",
                   command=self.app.open_diagnostics).pack(
            anchor="w", padx=10, pady=6)
        from las import __version__
        ttk.Label(tab, text=f"Engine: Local Agent Studio {__version__} · "
                            "SQLite event store · deterministic replay",
                  style="Muted.TLabel", wraplength=520).pack(
            anchor="w", padx=10, pady=6)
        ttk.Label(tab, text="Developer API toggle (P1)",
                  style="Muted.TLabel").pack(anchor="w", padx=10, pady=2)
