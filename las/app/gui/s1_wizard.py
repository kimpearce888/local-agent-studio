"""S1 Setup wizard (DST-2): five steps, Back always available.

Welcome (the privacy statement) → Detect servers (localhost probes) →
Credentials (only for servers that answered 401) → Models (with
capability warnings) → Self-test (a three-task plan with hard caps).
Finish is enabled after a pass; Skip is allowed and recorded.
"""

from __future__ import annotations

import re
import threading
import tkinter as tk
from tkinter import ttk

from las.app.gui.widgets import Dialog, error_dialog
from las.modules.providers import PRESETS, build_adapter, detect_servers
from las.modules.storage import ProviderProfile
from las.shared.provider_contract import ProviderError


class SetupWizard(ttk.Frame):
    STEPS = ["Welcome", "Detect servers", "Credentials", "Models",
             "Self-test"]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.theme = app.theme
        self.step = 0
        self.detected: list[dict] = []
        self.profile: ProviderProfile | None = None
        self.needs_key = False
        self.models: list[dict] = []
        self._build()
        self._render()

    # -- chrome -----------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, text="Set up Local Agent Studio",
                  style="Title.TLabel").pack(side="left")
        self.progress_lbl = ttk.Label(head, text="", style="Muted.TLabel")
        self.progress_lbl.pack(side="right")

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)

        nav = ttk.Frame(self)
        nav.pack(fill="x", pady=10)
        self.back_btn = ttk.Button(nav, text="‹ Back", command=self._back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next ›",
                                   style="Primary.TButton",
                                   command=self._next)
        self.next_btn.pack(side="right")
        self.skip_btn = ttk.Button(nav, text="Skip", command=self._skip)
        self.skip_btn.pack(side="right", padx=8)

    def _render(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.progress_lbl.configure(
            text=f"Step {self.step + 1} of 5 - {self.STEPS[self.step]}")
        self.back_btn.configure(state="normal" if self.step > 0
                                else "disabled")
        [self._welcome, self._detect, self._credentials, self._models,
         self._selftest][self.step]()

    def focus_regions(self) -> list:
        return [self.body]

    # -- step 1: welcome -----------------------------------------------------------

    def _welcome(self) -> None:
        ttk.Label(self.body, text="Welcome", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 6))
        text = ("Local Agent Studio runs on your machine.  Nothing "
                "leaves it except requests to the model servers you "
                "choose, and every run is stored locally in a tamper-"
                "evident event log you can export or delete.\n\n"
                "The next step looks for a local server on this "
                "machine (LM Studio, Ollama, llama.cpp, vLLM or Unsloth "
                "Studio).")
        ttk.Label(self.body, text=text, wraplength=560,
                  justify="left").pack(anchor="w")

    # -- step 2: detect ----------------------------------------------------------------

    def _detect(self) -> None:
        ttk.Label(self.body, text="Detect servers",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(self.body,
                  text="Probing localhost ports 1234, 11434, 8080, 8000 "
                       "and 8888 - one unauthenticated model-list request "
                       "each, 1 second timeout, no credentials sent, no "
                       "redirects followed.",
                  wraplength=560, justify="left").pack(anchor="w")
        self.detect_list = tk.Listbox(
            self.body, background=self.theme.c["surface"],
            foreground=self.theme.c["text"],
            selectbackground=self.theme.c["select"], height=6,
            relief="solid", borderwidth=1)
        self.detect_list.pack(fill="x", pady=8)
        row = ttk.Frame(self.body)
        row.pack(fill="x")
        ttk.Button(row, text="Scan again",
                   command=self._scan).pack(side="left")
        ttk.Button(row, text="Add manually…",
                   command=self._add_manual).pack(side="left", padx=8)
        self.scan_status = ttk.Label(self.body, text="", style="Muted.TLabel")
        self.scan_status.pack(anchor="w")
        self._scan()

    def _scan(self) -> None:
        self.scan_status.configure(text="Scanning…")
        self.detect_list.delete(0, "end")

        def work():
            found = detect_servers()
            self.after(0, lambda: self._scan_done(found))

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, found: list[dict]) -> None:
        self.detected = found
        if not found:
            self.scan_status.configure(
                text="No server found.  Start one, or Add manually.")
            self.detect_list.insert("end", "LM Studio - start the server "
                                           "and enable the local API "
                                           "(port 1234)")
            self.detect_list.insert("end", "Ollama - `ollama serve` "
                                           "(port 11434)")
            self.detect_list.insert("end", "llama.cpp server - `llama-server`"
                                           " (port 8080)")
            self.detect_list.insert("end", "vLLM (port 8000) / Unsloth "
                                           "Studio (port 8888)")
            return
        self.scan_status.configure(
            text=f"{len(found)} server(s) found - select one and press "
                 "Next")
        for s in found:
            self.detect_list.insert(
                "end", f"{s.get('type', 'server')} at "
                       f"{s.get('url', '')} "
                       f"({s.get('status', '')})")
        self.detect_list.selection_set(0)

    def _add_manual(self) -> None:
        dlg = Dialog(self.body, "Add a server manually",
                     buttons=[("Add", "Primary.TButton", "add"),
                              ("Cancel", "", "close")])
        name = tk.StringVar(value="My server")
        url = tk.StringVar(value="http://localhost:8000/v1")
        kind = tk.StringVar(value="generic")
        ttk.Label(dlg.body, text="Name").grid(row=0, column=0, sticky="w",
                                              padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=name).grid(row=0, column=1,
                                                    padx=6)
        ttk.Label(dlg.body, text="Base URL").grid(row=1, column=0,
                                                  sticky="w", padx=6, pady=4)
        ttk.Entry(dlg.body, textvariable=url).grid(row=1, column=1, padx=6)
        ttk.Label(dlg.body, text="Preset").grid(row=2, column=0, sticky="w",
                                                padx=6, pady=4)
        ttk.Combobox(dlg.body, textvariable=kind, state="readonly",
                     values=sorted(PRESETS) + ["generic"]).grid(
            row=2, column=1, padx=6)

        def add():
            self.detected = [{"type": kind.get(), "url": url.get(),
                              "status": "manual"}]
            self.profile = ProviderProfile(
                id=name.get().strip().lower().replace(" ", "-"),
                name=name.get().strip(), kind=kind.get(),
                base_url=url.get().strip())
            self.after(0, self._render)
            dlg.close()
        dlg.on_add = add
        dlg.wait_window()

    # -- step 3: credentials ------------------------------------------------------------

    def _credentials(self) -> None:
        ttk.Label(self.body, text="Credentials",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        if self.profile is None and self.detected:
            first = self.detected[0]
            pid = first.get("type", "generic")
            self.profile = ProviderProfile(
                id=pid, name=pid.title(), kind=pid,
                base_url=first.get("url", ""))
        if self.profile is None:
            ttk.Label(self.body, text="No server selected - go Back and "
                                      "scan again.").pack(anchor="w")
            return
        self.needs_key = str(
            (self.detected[0] or {}).get("status", "")).find("401") >= 0
        if not self.needs_key:
            ttk.Label(self.body,
                      text=f"{self.profile.name} answered without "
                           "credentials - local servers usually need "
                           "no key.  Nothing to enter here.",
                      wraplength=560).pack(anchor="w")
            return
        ttk.Label(self.body,
                  text=f"{self.profile.name} requires a key.  Create one "
                       "in LM Studio under server settings, API tokens; "
                       "in Unsloth Studio under Settings, API.  Keys are "
                       "stored by environment reference, never in plans "
                       "or events.",
                  wraplength=560).pack(anchor="w")
        self.key_var = tk.StringVar()
        ttk.Entry(self.body, textvariable=self.key_var,
                  show="•").pack(pady=8)

        def check():
            import os
            value = self.key_var.get().strip()
            if not value:
                return
            env = f"LAS_KEY_{self.profile.id.upper()}"
            os.environ[env] = value
            self.profile.auth_env = f"env:{env}"
            try:
                build_adapter(self.profile).list_models()
                self.app.live.configure(text="Key accepted")
                self._next()
            except ProviderError as exc:
                error_dialog(self.body, exc.code,
                             "The server rejected this key. Check that it "
                             "was copied completely.")
        ttk.Button(self.body, text="Check key",
                   command=check).pack()

    # -- step 4: models --------------------------------------------------------------------

    def _models(self) -> None:
        ttk.Label(self.body, text="Models",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        if self.profile is None:
            ttk.Label(self.body, text="No server selected.").pack(
                anchor="w")
            return
        # Persist the profile before probing models.
        self.app.store.save_provider(self.profile)
        try:
            models = build_adapter(self.profile).list_models()
        except ProviderError as exc:
            error_dialog(self.body, exc.code, str(exc))
            models = []
        self.models = models
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True, pady=4)
        ttk.Label(frame, text="Planner model").grid(row=0, column=0,
                                                    sticky="w", padx=6)
        ttk.Label(frame, text="Worker model").grid(row=1, column=0,
                                                   sticky="w", padx=6)
        ids = [m.get("id", "") for m in models] or ["(no models listed)"]
        self.planner_var = tk.StringVar(value=ids[0])
        self.worker_var = tk.StringVar(value=ids[0])
        ttk.Combobox(frame, textvariable=self.planner_var,
                     values=ids).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Combobox(frame, textvariable=self.worker_var,
                     values=ids).grid(row=1, column=1, sticky="w", padx=6)
        self.model_warn = ttk.Label(self.body, text="",
                                    style="Muted.TLabel",
                                    wraplength=560)
        self.model_warn.pack(anchor="w", pady=4)
        self.planner_var.trace_add(
            "write", lambda *_: self._model_warning())
        self._model_warning()

    def _model_warning(self) -> None:
        mid = self.planner_var.get()
        warns = []
        m = next((m for m in self.models if m.get("id") == mid), {})
        ctx = m.get("context_length")
        if ctx and int(ctx) < 8192:
            warns.append("Tasks will be limited to short inputs.")
        if re.search(r"[-_/]?([0-9]+(?:\.[0-9]+)?)b\b", mid.lower()):
            size = float(re.search(r"([0-9]+(?:\.[0-9]+)?)b",
                                   mid.lower()).group(1))
            if size < 7:
                warns.append("Small models may need several repair "
                             "rounds when planning.")
        self.model_warn.configure(text=" ".join(warns))

    # -- step 5: self-test -------------------------------------------------------------------

    def _selftest(self) -> None:
        ttk.Label(self.body, text="Self-test",
                  style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(self.body,
                  text="A built-in three-task plan with hard caps "
                       "(20,000 tokens, 5 minutes) runs against your "
                       "server.  Finish is enabled after a pass; Skip is "
                       "allowed and recorded.",
                  wraplength=560).pack(anchor="w")
        self.test_lbl = ttk.Label(self.body, text="", wraplength=560)
        self.test_lbl.pack(anchor="w", pady=8)

        def run_test():
            self.test_lbl.configure(text="Running the self-test…")
            from las.modules.selftest import run_selftest  # optional-dep: selftest
            try:
                adapter = build_adapter(self.profile)
                result = run_selftest(self.app.data, adapter)
            except Exception as exc:                    # noqa: BLE001
                result = {"ok": False, "detail": str(exc)}
            self.after(0, lambda: self._selftest_done(result))
        threading.Thread(target=run_test, daemon=True).start()

    def _selftest_done(self, result) -> None:
        if isinstance(result, dict):                   # error path
            ok, detail = bool(result.get("ok")), result.get("detail", "")
        else:
            ok = result.ok
            bits = [f"{s.get('name', '?')}: "
                    f"{'pass' if s.get('ok') else 'fail'}"
                    + (f" - {s['detail']}" if s.get("detail") else "")
                    for s in (result.steps or [])]
            detail = " · ".join(bits) or result.error
        icon = "✓" if ok else "✗"
        self.test_lbl.configure(
            text=f"{icon} {'Passed' if ok else 'Failed'}"
                 + (f" - {detail}" if detail else ""))
        self.next_btn.configure(
            text="Finish", state="normal" if ok else "disabled")

    # -- navigation -------------------------------------------------------------------------

    def _back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self.next_btn.configure(text="Next ›", state="normal")
            self._render()

    def _next(self) -> None:
        if self.step == 1 and self.detected and self.profile is None:
            first = self.detected[self.detect_list.curselection()[0]] \
                if self.detect_list.curselection() else self.detected[0]
            pid = first.get("type", "generic")
            self.profile = ProviderProfile(
                id=pid, name=pid.title(), kind=pid, base_url=first.get(
                    "url", ""))
        if self.step == 4:
            self._finish()
            return
        self.step = min(4, self.step + 1)
        if self.step == 4:
            self.skip_btn.configure(text="Skip self-test")
        self._render()

    def _skip(self) -> None:
        self.app.store.set_setting("selftest_skipped", True)
        self._finish()

    def _finish(self) -> None:
        if self.profile is not None:
            self.app.store.save_provider(self.profile)
        if hasattr(self, "planner_var"):
            self.app.store.set_setting("default_planner_model",
                                       self.planner_var.get())
            self.app.store.set_setting("default_worker_model",
                                       self.worker_var.get())
        self.app.store.set_setting("default_provider",
                                   self.profile.id if self.profile else "")
        self.app.live.configure(text="Setup complete - describe a goal "
                                     "to create your first plan")
        self.app.show("runs")
        self.app.open_new_goal()
