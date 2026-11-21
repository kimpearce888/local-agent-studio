"""Theming (spec 9: light, dark and high-contrast; text scale 100-200%).

Pure-tkinter application of the palette: ttk styles, the Tk palette,
canvas colors and fonts.  Switching themes at runtime re-applies
everything and re-renders the current screen.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Roles, not literal colors, so every consumer (chips, badges, edges)
# stays consistent across the three palettes.
LIGHT = {
    "name": "light",
    "bg": "#f5f5f4", "surface": "#ffffff", "surface2": "#ececea",
    "text": "#1c1917", "muted": "#57534e", "border": "#d6d3d1",
    "accent": "#1d4ed8", "accent_text": "#ffffff", "accent_soft": "#dbeafe",
    "success": "#15803d", "success_soft": "#dcfce7",
    "warning": "#b45309", "warning_soft": "#fef3c7",
    "danger": "#b91c1c", "danger_soft": "#fee2e2",
    "info": "#0e7490", "info_soft": "#cffafe",
    "neutral_soft": "#e7e5e4",
    "focus": "#1d4ed8", "select": "#dbeafe",
}

DARK = {
    "name": "dark",
    "bg": "#18181b", "surface": "#27272a", "surface2": "#3f3f46",
    "text": "#f4f4f5", "muted": "#a1a1aa", "border": "#52525b",
    "accent": "#93c5fd", "accent_text": "#0b1120", "accent_soft": "#1e3a8a",
    "success": "#86efac", "success_soft": "#14532d",
    "warning": "#fcd34d", "warning_soft": "#78350f",
    "danger": "#fca5a5", "danger_soft": "#7f1d1d",
    "info": "#67e8f9", "info_soft": "#155e75",
    "neutral_soft": "#3f3f46",
    "focus": "#93c5fd", "select": "#1e3a8a",
}

HIGH_CONTRAST = {
    "name": "high-contrast",
    "bg": "#000000", "surface": "#000000", "surface2": "#1a1a1a",
    "text": "#ffffff", "muted": "#e6e6e6", "border": "#ffffff",
    "accent": "#ffff00", "accent_text": "#000000", "accent_soft": "#4d4d00",
    "success": "#00ff00", "success_soft": "#003b00",
    "warning": "#ffff00", "warning_soft": "#4d4d00",
    "danger": "#ff5050", "danger_soft": "#4d0000",
    "info": "#00ffff", "info_soft": "#004d4d",
    "neutral_soft": "#333333",
    "focus": "#ffff00", "select": "#4d4d00",
}

PALETTES = {"light": LIGHT, "dark": DARK, "high-contrast": HIGH_CONTRAST}

BASE_FONT_SIZE = 10


class Theme:
    """The active palette plus the derived widget style set."""

    def __init__(self, root: tk.Misc, mode: str = "light", text_scale: int = 100,
                 reduce_motion: bool = False):
        self.root = root
        self.mode = mode if mode in PALETTES else "light"
        self.text_scale = max(100, min(200, int(text_scale)))
        self.reduce_motion = reduce_motion
        self._observers: list = []
        self._style = ttk.Style(root)

    # -- palette access -----------------------------------------------------

    @property
    def c(self) -> dict:
        return PALETTES[self.mode]

    def font(self, size_delta: int = 0, weight: str = "normal",
             family: str = "") -> tuple:
        size = max(8, round(BASE_FONT_SIZE * self.text_scale / 100) + size_delta)
        fam = family or "TkDefaultFont"
        return (fam, size, weight) if not family else (family, size, weight)

    def scaled(self, px: int) -> int:
        return max(px, round(px * self.text_scale / 100))

    # -- observation --------------------------------------------------------

    def observe(self, callback) -> None:
        self._observers.append(callback)

    def set_mode(self, mode: str) -> None:
        if mode in PALETTES and mode != self.mode:
            self.mode = mode
            self.apply()
            for cb in self._observers:
                cb()

    def set_text_scale(self, scale: int) -> None:
        scale = max(100, min(200, int(scale)))
        if scale != self.text_scale:
            self.text_scale = scale
            self.apply()
            for cb in self._observers:
                cb()

    def set_reduce_motion(self, value: bool) -> None:
        self.reduce_motion = bool(value)
        for cb in self._observers:
            cb()

    # -- application --------------------------------------------------------

    def apply(self) -> None:
        c = self.c
        try:
            self.root.tk_setPalette(
                background=c["bg"], foreground=c["text"],
                activeBackground=c["surface2"], activeForeground=c["text"],
                highlightBackground=c["border"], highlightColor=c["focus"],
                selectBackground=c["select"], selectForeground=c["text"],
                insertBackground=c["text"])
        except tk.TclError:
            pass                      # headless test builds without a palette
        s = self._style
        base = self.font()
        bold = self.font(0, "bold")
        small = self.font(-1)

        s.configure(".", background=c["bg"], foreground=c["text"],
                    font=base, borderwidth=1, focuscolor=c["focus"])
        s.configure("TFrame", background=c["bg"], borderwidth=0)
        s.configure("Surface.TFrame", background=c["surface"])
        s.configure("Rail.TFrame", background=c["surface2"])
        s.configure("TLabel", background=c["bg"], foreground=c["text"],
                    font=base)
        s.configure("Muted.TLabel", foreground=c["muted"], font=small)
        s.configure("Title.TLabel", font=self.font(3, "bold"))
        s.configure("Heading.TLabel", font=bold)
        s.configure("Surface.TLabel", background=c["surface"])
        s.configure("Rail.TLabel", background=c["surface2"])
        s.configure("RailActive.TLabel", background=c["accent"],
                    foreground=c["accent_text"])
        s.configure("TButton", background=c["surface"], foreground=c["text"],
                    padding=(10, 5), font=base, borderwidth=1)
        s.map("TButton",
              background=[("active", c["surface2"]), ("disabled", c["surface"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Primary.TButton", background=c["accent"],
                    foreground=c["accent_text"], font=bold)
        s.map("Primary.TButton",
              background=[("active", c["accent_soft"]), ("disabled", c["surface2"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Danger.TButton", background=c["danger"],
                    foreground="#ffffff" if self.mode != "dark" else "#000000")
        s.configure("TEntry", fieldbackground=c["surface"], foreground=c["text"],
                    insertcolor=c["text"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], padding=4)
        s.configure("TSpinbox", fieldbackground=c["surface"],
                    foreground=c["text"], arrowcolor=c["text"],
                    bordercolor=c["border"], padding=4)
        s.configure("TCombobox", fieldbackground=c["surface"],
                    foreground=c["text"], bordercolor=c["border"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", c["surface"])],
              foreground=[("readonly", c["text"])])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["text"],
                    focuscolor=c["focus"], font=base)
        s.map("TCheckbutton", background=[("active", c["bg"])],
              indicatorcolor=[("selected", c["accent"]), ("!selected", c["surface"])])
        s.configure("TRadiobutton", background=c["bg"], foreground=c["text"],
                    focuscolor=c["focus"], font=base)
        s.map("TRadiobutton", background=[("active", c["bg"])])
        s.configure("TLabelframe", background=c["bg"], foreground=c["text"],
                    bordercolor=c["border"], relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=c["bg"], font=bold)
        s.configure("TNotebook", background=c["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=c["surface2"],
                    foreground=c["text"], padding=(12, 6), font=base)
        s.map("TNotebook.Tab",
              background=[("selected", c["accent"])],
              foreground=[("selected", c["accent_text"])])
        s.configure("Treeview", background=c["surface"], foreground=c["text"],
                    fieldbackground=c["surface"], bordercolor=c["border"],
                    rowheight=max(24, self.scaled(22)), font=base)
        s.configure("Treeview.Heading", background=c["surface2"],
                    foreground=c["text"], font=bold, relief="flat")
        s.map("Treeview",
              background=[("selected", c["select"])],
              foreground=[("selected", c["text"])])
        s.configure("TScrollbar", background=c["surface2"],
                    troughcolor=c["bg"], bordercolor=c["bg"], arrowcolor=c["text"])
        s.configure("TScale", background=c["bg"], troughcolor=c["surface2"],
                    bordercolor=c["border"])
        s.configure("TProgressbar", background=c["accent"],
                    troughcolor=c["surface2"], bordercolor=c["border"],
                    lightcolor=c["accent"], darkcolor=c["accent"])
        s.configure("TSeparator", background=c["border"])
        s.configure("TListbox", background=c["surface"], foreground=c["text"],
                    font=base)

    # -- status helpers (icons + text: colour is never the only signal) ------

    def status_appearance(self, status: str) -> tuple[str, str, str]:
        """(icon, plain text, role color) for a run/task status."""
        table = {
            "queued": ("○", "Queued", "muted"),
            "ready": ("○", "Ready", "muted"),
            "running": ("▶", "Running", "info"),
            "succeeded": ("✓", "Succeeded", "success"),
            "failed": ("✗", "Failed", "danger"),
            "cancelled": ("⨯", "Cancelled", "muted"),
            "paused": ("⏸", "Paused", "warning"),
            "awaiting_approval": ("⚑", "Needs you", "warning"),
            "skipped": ("⤼", "Skipped", "muted"),
            "stale": ("↻", "Stale", "warning"),
            "pending": ("·", "Pending", "muted"),
            "planning": ("⋯", "Planning", "info"),
        }
        icon, text, role = table.get(status, ("?", status, "muted"))
        return icon, text, role

    def role_color(self, role: str) -> str:
        return self.c.get(role, self.c["text"])


def default_theme(root: tk.Misc, settings_reader) -> Theme:
    """Build the theme from stored settings (S11 Accessibility keys)."""
    def setting(key, fallback):
        try:
            return settings_reader(key) or fallback
        except Exception:
            return fallback
    theme = Theme(
        root,
        mode=setting("theme", "light"),
        text_scale=setting("text_scale", 100),
        reduce_motion=bool(setting("reduce_motion", False)))
    theme.apply()
    return theme
