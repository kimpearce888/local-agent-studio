"""Shared GUI widgets (spec 4 and 9).

Every status indicator is an icon plus text - colour is never the only
signal.  Dialogs trap focus and return it to the control that opened
them.  The live region gives screen-reader-style announcements: polite
for progress, assertive for failures and gates.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from las.shared.messages import message_with_fix

DIALOG_MIN_TARGET = 24   # spec 9: interactive targets at least 24x24


class LiveRegion(ttk.Label):
    """Announcement area; updated via :meth:`say`."""

    def __init__(self, parent, **kw):
        kw.setdefault("text", "")
        super().__init__(parent, **kw)
        self.configure(anchor="w")

    def say(self, text: str, assertive: bool = False) -> None:
        # Re-assigning the variable content triggers reader refresh in
        # real toolkit bindings; here it also drives test assertions.
        self.configure(text=text)


class StatusChip(ttk.Frame):
    """`icon + text` chip for run and task statuses."""

    def __init__(self, parent, theme, status: str = "queued", **kw):
        super().__init__(parent, **kw)
        self.theme = theme
        self._build(status)

    def _build(self, status: str) -> None:
        for w in self.winfo_children():
            w.destroy()
        icon, text, role = self.theme.status_appearance(status)
        c = self.theme.c
        soft = self.theme.c.get(f"{role}_soft", c["neutral_soft"])
        self.configure(style="TFrame")
        self._frame = tk.Frame(self, background=soft)
        self._frame.pack(padx=1, pady=1)
        self._icon = tk.Label(self._frame, text=icon, background=soft,
                              foreground=self.theme.role_color(role),
                              font=self.theme.font(0, "bold"), width=2)
        self._icon.pack(side="left", padx=(6, 2), pady=2)
        self._text = tk.Label(self._frame, text=text, background=soft,
                              foreground=c["text"],
                              font=self.theme.font(-1))
        self._text.pack(side="left", padx=(0, 6), pady=2)

    def set(self, status: str) -> None:
        self._build(status)

    def text(self) -> str:
        return self._text.cget("text")


class CapabilityBadge(ttk.Frame):
    """The A/B/C risk indicators (spec S4): three labelled boxes plus
    `n of 3` and a `Needs a gate` marker at 3 of 3."""

    LETTERS = ("A", "B", "C")
    LABELS = ("untrusted input", "sensitive access", "external effect")

    def __init__(self, parent, theme, flags=(False, False, False), **kw):
        super().__init__(parent, **kw)
        self.theme = theme
        self._build(flags)

    def _build(self, flags) -> None:
        for w in self.winfo_children():
            w.destroy()
        c = self.theme.c
        filled = sum(1 for f in flags if f)
        parts = []
        for letter, label, on in zip(self.LETTERS, self.LABELS, flags):
            bg = c["danger_soft"] if on else c["neutral_soft"]
            fg = self.theme.role_color("danger") if on else c["muted"]
            box = tk.Label(self, text=letter, background=bg, foreground=fg,
                           font=self.theme.font(-1, "bold"), width=1,
                           relief="solid", borderwidth=1, padx=2, pady=0)
            box.pack(side="left", padx=1)
            box.tooltip_text = f"{letter}: {label}" + (" (set)" if on else "")
            parts.append(box)
        self._count = tk.Label(
            self, text=f"{filled} of 3", foreground=c["muted"],
            font=self.theme.font(-2))
        self._count.pack(side="left", padx=4)
        if filled == 3:
            self._gate = tk.Label(
                self, text="Needs a gate", background=c["danger"],
                foreground="#ffffff", font=self.theme.font(-2, "bold"))
            self._gate.pack(side="left", padx=2)

    def set(self, flags) -> None:
        self._build(flags)


class BudgetMeter(ttk.Frame):
    """Token and wall-clock caps with the 80% amber / 100% stop states."""

    def __init__(self, parent, theme, **kw):
        super().__init__(parent, **kw)
        self.theme = theme
        c = theme.c
        self._var = tk.DoubleVar(value=0.0)
        self._label = tk.Label(self, text="0% of the token cap",
                               background=c["surface2"],
                               foreground=c["muted"],
                               font=theme.font(-2), anchor="e")
        self._label.pack(side="left", padx=(0, 4))
        self._bar = ttk.Progressbar(self, length=110, maximum=100,
                                    variable=self._var, mode="determinate")
        self._bar.pack(side="left")

    def update_usage(self, used: int, cap: int, elapsed: float = None,
                     cap_minutes: float = None) -> None:
        fraction = min(1.0, used / cap) if cap else 0.0
        self._var.set(fraction * 100)
        pct = round(fraction * 100)
        c = self.theme.c
        if fraction >= 1.0:
            self._bar.configure(style="Danger.Horizontal.TProgressbar")
            text = "100% of the token cap"
        elif fraction >= 0.8:
            self._bar.configure(style="Warning.Horizontal.TProgressbar")
            text = f"{pct}% of the token cap"
        else:
            self._bar.configure(style="TProgressbar")
            text = f"{pct}% of the token cap"
        if elapsed is not None and cap_minutes:
            mins = elapsed / 60.0
            text += f" · {mins:.0f}/{cap_minutes:.0f} min"
        self._label.configure(text=text, foreground=c["text"])


class Dialog(tk.Toplevel):
    """A modal dialog that traps focus (spec 9) and restores it on close."""

    def __init__(self, parent, title: str, buttons=(), **kw):
        super().__init__(parent, **kw)
        self.parent_widget = parent
        self.title(title)
        self.result = None
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        c = None
        try:
            from las.app.gui.app import current_theme
            c = current_theme().c
        except Exception:
            c = {"bg": "#f5f5f4", "surface": "#ffffff", "text": "#1c1917",
                 "muted": "#57534e", "border": "#d6d3d1", "accent": "#1d4ed8",
                 "accent_text": "#ffffff"}
        self.configure(background=c["bg"])
        self._c = c
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=16, pady=12)
        if buttons:
            row = ttk.Frame(self)
            row.pack(fill="x", padx=16, pady=(0, 12))
            for text, style, cmd in buttons:
                btn = ttk.Button(row, text=text, style=style,
                                 command=lambda t=text, k=cmd: self._act(t, k))
                btn.pack(side="right", padx=4,
                         ipady=max(0, DIALOG_MIN_TARGET // 2 - 6))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._restore_focus = parent.focus_get()
        self.wait_visibility()
        self.grab_set()
        self.bind("<Tab>", self._trap_focus, add=True)
        self.bind("<Shift-Tab>", self._trap_focus, add=True)

    def _act(self, text, kind):
        if kind == "close":
            self.close()
        else:
            handler = getattr(self, f"on_{kind}", None)
            if handler:
                handler()
            self.close()

    def _trap_focus(self, event):
        widgets = [w for w in self.winfo_children()]
        focusable = []
        stack = list(widgets)
        while stack:
            w = stack.pop()
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass
            if isinstance(w, (ttk.Button, ttk.Entry, ttk.Combobox,
                              ttk.Checkbutton, ttk.Radiobutton, ttk.Spinbox,
                              ttk.Treeview, tk.Text, tk.Listbox, tk.Canvas)):
                focusable.append(w)
        if not focusable:
            return
        current = self.focus_get()
        if current in focusable:
            i = focusable.index(current)
            if event.keysym == "Tab":
                nxt = focusable[(i + 1) % len(focusable)]
            else:
                nxt = focusable[(i - 1) % len(focusable)]
            nxt.focus_set()
            return "break"

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self._restore_focus is not None and self._restore_focus.winfo_exists():
            try:
                self._restore_focus.focus_set()
            except tk.TclError:
                pass


def confirm(parent, text: str, title: str = "Confirm",
            danger: bool = False) -> bool:
    dlg = Dialog(parent, title, buttons=[("Cancel", "", "close"),
                                         ("OK", "Danger.TButton" if danger
                                          else "Primary.TButton", "ok")])
    dlg.on_ok = lambda: setattr(dlg, "result", True)
    ttk.Label(dlg.body, text=text, wraplength=360).pack(padx=8, pady=12)
    dlg.wait_window()
    return dlg.result is True


def error_dialog(parent, code: str, detail: str = "") -> None:
    """Message-catalogue error dialog: code, plain text and a fix."""
    text, fix = message_with_fix(code)
    dlg = Dialog(parent, f"Error {code}", buttons=[("Close", "", "close")])
    body = dlg.body
    ttk.Label(body, text=text, style="Heading.TLabel",
              wraplength=360).pack(anchor="w", pady=(0, 6))
    if detail:
        ttk.Label(body, text=detail, style="Muted.TLabel",
                  wraplength=360).pack(anchor="w", pady=(0, 6))
    ttk.Label(body, text=fix, wraplength=360).pack(anchor="w")
    dlg.wait_window()


def info_dialog(parent, title: str, text: str) -> None:
    dlg = Dialog(parent, title, buttons=[("Close", "", "close")])
    ttk.Label(dlg.body, text=text, wraplength=360).pack(padx=8, pady=12)
    dlg.wait_window()


class EmptyState(ttk.Frame):
    """Spec empty states, e.g. S2: text plus a primary action."""

    def __init__(self, parent, text: str, action_text: str = "",
                 action=None, **kw):
        super().__init__(parent, **kw)
        box = ttk.Frame(self)
        box.place(relx=0.5, rely=0.45, anchor="center")
        ttk.Label(box, text=text, style="Title.TLabel",
                  wraplength=420, justify="center").pack(pady=(0, 16))
        if action_text and action:
            ttk.Button(box, text=action_text, style="Primary.TButton",
                       command=action).pack(ipady=4)
