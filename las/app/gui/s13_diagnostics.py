"""S13 Diagnostics (OBS-7): the redacted bundle a user can attach to a
bug report.  Lists every included item, applies redaction (keys
removed, prompt text replaced by lengths and hashes), lets the user
open the file before saving, and sends nothing anywhere.
"""

from __future__ import annotations

import json
import os
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog

from las.app.gui.widgets import Dialog
from las.modules.diagnostics import build_diagnostics, diagnostics_items


class DiagnosticsDialog(Dialog):
    def __init__(self, parent, app):
        self.app = app
        super().__init__(parent, "Diagnostics",
                         buttons=[("Save…", "Primary.TButton", "save"),
                                  ("Open preview", "", "preview"),
                                  ("Close", "", "close")])
        ttk.Label(self.body,
                  text="Everything below goes into one file you can attach "
                       "to a bug report.  Keys are removed; prompt text is "
                       "replaced by lengths and hashes.  Nothing is sent "
                       "anywhere.",
                  wraplength=440).pack(anchor="w", pady=(0, 8))
        tree = ttk.Treeview(self.body, columns=("item",), show="headings",
                            height=10)
        tree.heading("item", text="Included")
        tree.column("item", width=430, anchor="w")
        for item in diagnostics_items():
            tree.insert("", "end", values=(item,))
        tree.pack(fill="both", expand=True)
        self._path = os.path.join(
            tempfile.gettempdir(), "las-diagnostics-preview.json")

    def _bundle(self) -> dict:
        from las import __version__
        modules = [
            {"id": "gui", "version": __version__, "core": "tkinter",
             "enabled": True, "summary": "desktop app"}]
        return build_diagnostics(self.app.store, self.app.engine, modules)

    def on_preview(self) -> None:
        bundle = self._bundle()
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=1, ensure_ascii=False)
        import subprocess
        import sys
        try:
            if sys.platform.startswith("win"):
                os.startfile(self._path)              # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self._path])
            else:
                subprocess.Popen(["xdg-open", self._path])
            self.app.live.configure(text="Diagnostics preview opened")
        except Exception:                              # noqa: BLE001
            self.app.live.configure(
                text=f"Preview written to {self._path}")

    def on_save(self) -> None:
        bundle = self._bundle()
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile="las-diagnostics.json")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=1, ensure_ascii=False)
        self.app.live.configure(
            text=f"Diagnostics saved to {os.path.basename(path)} "
                 f"({os.path.getsize(path):,} bytes)")
