"""The application shell (spec 3): left navigation, top bar, screens,
command palette, keyboard shortcuts and the run-session poller.

Screens are classes in this package with the constructor signature
``Screen(parent, app)`` and an optional ``on_show()`` hook.
"""

from __future__ import annotations

import os
import queue
import tkinter as tk
from tkinter import ttk

from las import __version__
from las.modules.execution import Engine
from las.modules.storage import Store
from las.app.gui.theme import Theme, default_theme

NAV_ITEMS = [
    # (key, label, icon)  - icon + text, never icon alone
    ("runs", "Runs", "▤"),
    ("studio", "Studio", "◈"),
    ("library", "Library", "◧"),
    ("providers", "Providers", "⇄"),
    ("settings", "Settings", "⚙"),
]

DEFAULT_SHORTCUTS = {
    # action -> Tk event pattern (spec 9; editable in Settings)
    "palette": "<Control-k>",
    "new_goal": "<Control-n>",
    "run": "<Control-Return>",
    "pause_after": "<Control-period>",
    "undo": "<Control-z>",
    "redo": "<Control-Shift-Z>",
    "move_earlier": "<Alt-Up>",
    "move_later": "<Alt-Down>",
    "open_inspector": "<Return>",
    "switch_view": "<Control-Shift-L>",
    "next_region": "<F6>",
    "shortcuts_help": "<Control-slash>",
}

SHORTCUT_HELP = [
    ("Command palette", "palette"),
    ("New goal", "new_goal"),
    ("Run", "run"),
    ("Pause after this task", "pause_after"),
    ("Undo / Redo", "undo"),
    ("Redo", "redo"),
    ("Move task earlier / later", "move_earlier"),
    ("Open inspector for selected task", "open_inspector"),
    ("Switch Canvas / List view", "switch_view"),
    ("Next region", "next_region"),
    ("Show shortcuts", "shortcuts_help"),
]

_THEME: Theme | None = None


def current_theme() -> Theme:
    if _THEME is None:
        raise RuntimeError("theme not initialised")
    return _THEME


def data_dir() -> str:
    """Same resolution as the CLI (spec 7.4)."""
    return os.environ.get("LAS_DATA_DIR") or os.path.expanduser(
        os.path.join("~", ".local-agent-studio"))


class StudioApp(tk.Tk):
    def __init__(self, dir_: str | None = None):
        super().__init__()
        global _THEME
        self.data = dir_ or data_dir()
        os.makedirs(self.data, exist_ok=True)
        self.store = Store(self.data)
        self.engine = Engine(self.data)

        self.theme = default_theme(self, self.store.get_setting)
        _THEME = self.theme

        self.title(f"Local Agent Studio {__version__}")
        self.geometry("1280x820")
        self.minsize(980, 640)

        self.plan = None                 # the plan open in Studio (S4)
        self.plan_origin_run = None      # run id when opened from a run
        self.session = None              # active RunSession
        self._session_ui = None          # runview currently bound to it
        self._region_index = 0
        self._palette = None

        self._build_shell()
        self._bind_shortcuts()
        self._build_screens()

        # Start where it makes sense: the wizard on first launch,
        # otherwise the Runs list (spec 3).
        providers = self.store.list_providers()
        if not providers and self.screen_class("wizard") is not None:
            self.show("wizard")
        else:
            self.show("runs")
        self.after(120, self._poll_session)

    # -- shell ---------------------------------------------------------------

    def _build_shell(self) -> None:
        c = self.theme.c
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=0)

        # Left navigation
        self.nav = tk.Frame(self, background=c["surface2"], width=170)
        self.nav.grid(row=0, column=0, rowspan=3, sticky="nsw")
        self.nav.pack_propagate(False)
        brand = tk.Label(self.nav, text="Local Agent Studio",
                         background=c["surface2"], foreground=c["text"],
                         font=self.theme.font(1, "bold"), anchor="w")
        brand.pack(fill="x", padx=14, pady=(16, 10))
        ver = tk.Label(self.nav, text=f"version {__version__}",
                       background=c["surface2"], foreground=c["muted"],
                       font=self.theme.font(-2), anchor="w")
        ver.pack(fill="x", padx=14, pady=(0, 12))
        self._nav_buttons = {}
        for key, label, icon in NAV_ITEMS:
            btn = tk.Button(
                self.nav, text=f" {icon}  {label}", anchor="w",
                background=c["surface2"], foreground=c["text"],
                activebackground=c["accent_soft"], activeforeground=c["text"],
                relief="flat", bd=0, font=self.theme.font(),
                padx=14, pady=8, cursor="hand2",
                command=lambda k=key: self.show(k))
            btn.pack(fill="x")
            self._nav_buttons[key] = btn
        self._nav_state()

        # Top bar (spec 3): goal title, status chip, provider health,
        # budget meter, palette button.
        top = ttk.Frame(self)
        top.grid(row=0, column=1, sticky="ew", padx=10, pady=(8, 2))
        self.top_goal = ttk.Label(top, text="No plan open",
                                  style="Muted.TLabel", anchor="w")
        self.top_goal.pack(side="left")
        self.top_chip_holder = ttk.Frame(top)
        self.top_chip_holder.pack(side="left", padx=12)
        from las.app.gui.widgets import StatusChip
        self.top_chip = StatusChip(self.top_chip_holder, self.theme, "queued")
        self.top_chip.pack(anchor="w")
        self.top_chip.pack_forget()          # shown while a run is active
        self.provider_health = ttk.Label(top, text="● Provider: not checked",
                                          style="Muted.TLabel")
        self.provider_health.pack(side="left", padx=12)
        self.budget = None                   # created by S6 during runs
        ttk.Button(top, text="⌘ Command palette  (Ctrl+K)",
                   command=self.open_palette).pack(side="right")

        # Content
        self.content = ttk.Frame(self)
        self.content.grid(row=1, column=1, sticky="nsew", padx=10, pady=6)

        # Status bar / live region (spec 9)
        from las.app.gui.widgets import LiveRegion
        self.live = LiveRegion(self, text="", style="Muted.TLabel",
                               anchor="w")
        self.live.grid(row=2, column=1, sticky="ew", padx=12, pady=(0, 6))

    def _nav_state(self) -> None:
        c = self.theme.c
        for key, btn in self._nav_buttons.items():
            active = getattr(self, "_current", None) == key
            studio_ok = key != "studio" or self.plan is not None
            btn.configure(
                state="normal" if studio_ok else "disabled",
                background=c["accent"] if active else c["surface2"],
                foreground=(c["accent_text"] if active
                            else (c["text"] if studio_ok else c["muted"])))

    def _build_screens(self) -> None:
        self._screens: dict[str, ttk.Frame] = {}
        self._screen_classes = {}
        # Lazy: built on first show() so startup stays fast.
        self._built = set()

    def screen_class(self, key: str):
        table = {
            "runs": ("las.app.gui.s2_runs", "RunsScreen"),
            "studio": ("las.app.gui.s4_studio", "StudioScreen"),
            "goal": ("las.app.gui.s3_goal", "NewGoalScreen"),
            "runview": ("las.app.gui.s6_runview", "RunViewScreen"),
            "providers": ("las.app.gui.s9_providers", "ProvidersScreen"),
            "library": ("las.app.gui.s10_library", "LibraryScreen"),
            "settings": ("las.app.gui.s11_settings", "SettingsScreen"),
            "wizard": ("las.app.gui.s1_wizard", "SetupWizard"),
        }
        if key not in table:
            return None
        import importlib
        mod_name, cls_name = table[key]
        if key not in self._screen_classes:
            self._screen_classes[key] = getattr(importlib.import_module(mod_name),
                                                cls_name)
        return self._screen_classes[key]

    def show(self, key: str, **ctx) -> None:
        """Show a screen by key, building it on first use."""
        cls = self.screen_class(key)
        if cls is None:
            return
        if key not in self._built:
            frame = cls(self.content, self)
            frame.grid(row=0, column=0, sticky="nsew")
            self._screens[key] = frame
            self._built.add(key)
        self.content.grid_propagate(True)
        for k, f in self._screens.items():
            if k == key:
                f.grid()
                if hasattr(f, "on_show"):
                    f.on_show(**ctx)
            else:
                f.grid_remove()
        self._current = key
        self._nav_state()

    # -- context opens -------------------------------------------------------

    def open_new_goal(self) -> None:
        self.show("goal")

    def open_studio(self, plan, run_id: str | None = None) -> None:
        self.plan = plan
        self.plan_origin_run = run_id
        self.top_goal.configure(text=plan.title[:60] if plan else "No plan open")
        self.show("studio")
        screen = self._screens.get("studio")
        if screen is not None:
            screen.load_plan(plan, run_id=run_id)

    def open_run(self, run_id: str) -> None:
        self.show("runview", run_id=run_id)

    def open_export(self, run_id: str) -> None:
        from las.app.gui.s12_export import ExportDialog
        ExportDialog(self, self, run_id)

    def open_context_preview(self, run_id: str, task_id: str) -> None:
        from las.app.gui.s5_context import ContextPreviewDialog
        ContextPreviewDialog(self, self, run_id, task_id)

    # -- run sessions --------------------------------------------------------

    def start_run(self, run_id: str, models: dict, config) -> None:
        from las.app.gui.runner import RunSession
        self.session = RunSession(self.engine, run_id,
                                  self._provider_registry(), models, config)
        self._session_ui = self._screens.get("runview")
        self.session.start()
        self.show("runview", run_id=run_id)
        self._session_ui = self._screens.get("runview")
        chip = self.top_chip
        chip.set("running")
        chip.pack(anchor="w")
        self.live.configure(text="Run started")

    def _provider_registry(self):
        """Registry with the network-log proxy on every adapter (S11)."""
        from las.modules.providers import ProviderRegistry, build_adapter
        from las.modules.storage import ProviderProfile
        from las.app.gui.netlog import LoggingAdapter
        if not hasattr(self, "_netlog"):
            self._netlog: list[dict] = []
        registry = ProviderRegistry()
        for p in self.store.list_providers():
            try:
                profile = ProviderProfile.from_obj(p) if isinstance(p, dict) else p
                registry.register(
                    profile.id,
                    LoggingAdapter(build_adapter(profile), self._netlog,
                                   self.store))
            except Exception:                      # noqa: BLE001
                continue
        return registry

    def default_models(self) -> dict:
        """Planner/worker from settings, else the first configured provider."""
        pid = self.store.get_setting("default_provider") or ""
        if not pid:
            providers = self.store.list_providers()
            if providers:
                pid = (providers[0].get("id")
                       if isinstance(providers[0], dict) else providers[0].id)
        if not pid:
            return {}
        worker = self.store.get_setting("default_worker_model") or ""
        planner = self.store.get_setting("default_planner_model") or worker
        if not worker:
            try:
                models = self._provider_registry().get(pid).list_models()
                worker = models[0]["id"] if models else ""
                planner = planner or worker
            except Exception:                      # noqa: BLE001
                return {"default": (pid, "")}
        return {"default": (pid, worker), "planner": (pid, planner)}

    def _poll_session(self) -> None:
        """Drain session events to the run view (never blocks the UI)."""
        if self.session is not None:
            drained = 0
            while drained < 50:
                try:
                    kind, payload = self.session.events.get_nowait()
                except queue.Empty:
                    break
                drained += 1
                self._handle_session_event(kind, payload)
        self.after(120, self._poll_session)

    def _handle_session_event(self, kind: str, payload: dict) -> None:
        runview = self._screens.get("runview")
        if runview is not None and runview.winfo_exists():
            runview.on_session_event(kind, payload)
        if kind == "finished":
            status = {"run_succeeded": "succeeded",
                      "run_failed": "failed",
                      "cancelled": "cancelled"}.get(payload.get("kind"),
                                                    "failed")
            self.top_chip.set(status)
            self.live.configure(
                text=f"Run {status}", foreground=self.theme.c["text"])
            self.live.say(f"Run {status}",
                          assertive=status != "succeeded")
        elif kind == "waiting":
            reason = payload.get("reason")
            if reason == "gate":
                self.top_chip.set("awaiting_approval")
                self.live.configure(text="A task needs your approval")
            elif reason == "failure":
                self.top_chip.set("failed")
                self.live.configure(text="A task failed")

    # -- command palette (Ctrl+K) --------------------------------------------

    def palette_actions(self) -> list[tuple[str, object]]:
        actions = [
            ("New goal", self.open_new_goal),
            ("Go to Runs", lambda: self.show("runs")),
            ("Go to Studio", lambda: self.show("studio")),
            ("Go to Library", lambda: self.show("library")),
            ("Go to Providers", lambda: self.show("providers")),
            ("Go to Settings", lambda: self.show("settings")),
            ("Toggle dark / light theme", self.toggle_theme),
            ("Diagnostics bundle", self.open_diagnostics),
        ]
        if self.plan is not None:
            actions.append(("Run plan", self.run_current_plan))
        return actions

    def toggle_theme(self) -> None:
        self.theme.set_mode("dark" if self.theme.mode == "light" else "light")

    def run_current_plan(self) -> None:
        screen = self._screens.get("studio")
        if screen is not None:
            screen.run_plan()

    def open_diagnostics(self) -> None:
        from las.app.gui.s13_diagnostics import DiagnosticsDialog
        DiagnosticsDialog(self, self)

    def open_palette(self) -> None:
        if self._palette is not None and self._palette.winfo_exists():
            self._palette.destroy()
            self._palette = None
            return
        dlg = tk.Toplevel(self)
        self._palette = dlg
        dlg.title("Command palette")
        dlg.transient(self)
        dlg.configure(background=self.theme.c["surface"])
        dlg.geometry("+%d+%d" % (self.winfo_rootx() + 220,
                                 self.winfo_rooty() + 90))
        entry = ttk.Entry(dlg, font=self.theme.font(2))
        entry.pack(fill="x", padx=12, pady=(12, 6))
        entry.focus_set()

        listbox = tk.Listbox(dlg, background=self.theme.c["surface"],
                             foreground=self.theme.c["text"],
                             font=self.theme.font(), height=min(
                                 12, len(self.palette_actions())),
                             activestyle="dotbox", bd=0,
                             highlightthickness=0, selectbackground=(
                                 self.theme.c["select"]))
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        actions = self.palette_actions()
        labels = [a[0] for a in actions]
        listbox.insert("end", *labels)

        def filter_actions(*_):
            text = entry.get().lower().strip()
            listbox.delete(0, "end")
            for label, _cb in actions:
                if not text or text in label.lower():
                    listbox.insert("end", label)

        def run_action(_event=None):
            sel = listbox.curselection()
            if not sel:
                return
            label = listbox.get(sel[0])
            dlg.destroy()
            self._palette = None
            for lbl, cb in actions:
                if lbl == label:
                    cb()
                    break

        entry.bind("<KeyRelease>", filter_actions)
        for key in ("<Return>", "<KP_Enter>"):
            entry.bind(key, run_action)
            listbox.bind(key, run_action)
        listbox.bind("<Double-Button-1>", run_action)
        dlg.bind("<Escape>", lambda e: (dlg.destroy(),
                                        setattr(self, "_palette", None)))
        dlg.grab_set()
        entry.focus_set()

    # -- keyboard (spec 9) ----------------------------------------------------

    def shortcut(self, action: str) -> str:
        overrides = self.store.get_setting("shortcuts") or {}
        return overrides.get(action) or DEFAULT_SHORTCUTS[action]

    def _bind_shortcuts(self) -> None:
        bindings = [
            ("palette", lambda e: self.open_palette()),
            ("new_goal", lambda e: self.open_new_goal()),
            ("shortcuts_help", lambda e: self.show_shortcuts()),
        ]
        for action, handler in bindings:
            try:
                self.bind(self.shortcut(action), handler)
            except tk.TclError:
                continue

    def show_shortcuts(self) -> None:
        from las.app.gui.widgets import Dialog
        dlg = Dialog(self, "Keyboard shortcuts",
                     buttons=[("Close", "", "close")])
        for i, (label, action) in enumerate(SHORTCUT_HELP):
            row = ttk.Frame(dlg.body)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=34, anchor="w").grid(
                row=i, column=0, sticky="w")
            ttk.Label(row, text=self.shortcut(action).replace("<", "")
                      .replace(">", ""), style="Muted.TLabel").grid(
                row=i, column=1, sticky="w")
        dlg.wait_window()

    # -- regions (F6) ---------------------------------------------------------

    def focus_regions(self) -> list:
        screen = self._screens.get(getattr(self, "_current", None))
        regions = [self.nav]
        if screen is not None and hasattr(screen, "focus_regions"):
            regions.extend(screen.focus_regions())
        regions.append(self.live.master)
        return [r for r in regions if r.winfo_exists()]

    def cycle_region(self, event=None) -> None:
        regions = self.focus_regions()
        if not regions:
            return
        self._region_index = (self._region_index + 1) % len(regions)
        regions[self._region_index].focus_set()
        return "break"

    # -- teardown -------------------------------------------------------------

    def shutdown(self) -> None:
        try:
            if self.session is not None and self.session.is_alive():
                self.session.request_cancel()
                self.session.finished.wait(timeout=3)
        finally:
            try:
                self.store.close()
            finally:
                self.destroy()


def main(dir_: str | None = None) -> int:
    app = StudioApp(dir_)
    app.protocol("WM_DELETE_WINDOW", app.shutdown)
    app.mainloop()
    return 0
