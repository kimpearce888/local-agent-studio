"""S12 Export (OBS-4): report and bundle export.

Format choice, include toggles, redaction defaults (off for reports),
the AI-generated notice for Markdown, and a summary of what will be
included and its size before saving.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog

from las.app.gui.widgets import Dialog
from las.modules.reporting import (
    ExportOptions, export_json_bundle, export_markdown, export_run,
)


class ExportDialog(Dialog):
    def __init__(self, parent, app, run_id: str):
        self.app = app
        self.run_id = run_id
        self.state = app.engine.state(run_id)
        super().__init__(parent, f"Export run {run_id}",
                         buttons=[("Save…", "Primary.TButton", "save"),
                                  ("Cancel", "", "close")])

        body = self.body
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Format").grid(row=0, column=0, sticky="w",
                                            padx=6, pady=4)
        self.format_var = tk.StringVar(value="markdown")
        fmt = ttk.Frame(body)
        fmt.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Radiobutton(fmt, text="Markdown report",
                        variable=self.format_var, value="markdown",
                        command=self._summary).pack(side="left")
        ttk.Radiobutton(fmt, text="JSON bundle",
                        variable=self.format_var, value="json",
                        command=self._summary).pack(side="left", padx=12)

        self.include_plan = tk.BooleanVar(value=True)
        self.include_transcripts = tk.BooleanVar(value=True)
        self.include_files = tk.BooleanVar(value=False)
        self.redact = tk.BooleanVar(value=False)
        self.notice = tk.BooleanVar(value=True)
        for i, (label, var) in enumerate([
                ("Include plan", self.include_plan),
                ("Include transcripts", self.include_transcripts),
                ("Include workspace files", self.include_files),
                ("Redact prompt text", self.redact),
                ("AI-generated notice (Markdown)", self.notice)]):
            ttk.Checkbutton(body, text=label, variable=var,
                            command=self._summary).grid(
                row=1 + i, column=0, columnspan=2, sticky="w",
                padx=6, pady=2)

        self.summary = ttk.Label(body, text="", style="Muted.TLabel",
                                 wraplength=420, justify="left")
        self.summary.grid(row=6, column=0, columnspan=2, sticky="w",
                          padx=6, pady=(8, 0))
        self._summary()

    def _options(self) -> ExportOptions:
        return ExportOptions(
            include_plan=self.include_plan.get(),
            include_transcripts=self.include_transcripts.get(),
            include_workspace_files=self.include_files.get(),
            ai_notice=self.notice.get(),
            redact_prompts=self.redact.get())

    def _summary(self) -> None:
        opts = self._options()
        tasks = len(self.state.plan.tasks)
        done = sum(1 for t in self.state.tasks.values()
                   if t.state == "succeeded")
        parts = [f"{self.state.title[:40]} - {done}/{tasks} tasks, "
                 f"{self.state.tokens_used()} tokens"]
        if self.format_var.get() == "markdown":
            parts.append("Markdown report"
                         + (" with the AI-generated notice"
                            if opts.ai_notice else "") + ".")
        else:
            parts.append("JSON bundle (machine-readable, full events).")
        included = [label for label, on in [
                ("plan", opts.include_plan),
                ("handoffs", True),
                ("transcripts", opts.include_transcripts),
                ("workspace files", opts.include_workspace_files)] if on]
        parts.append("Includes: " + ", ".join(included) + ".")
        if opts.redact_prompts:
            parts.append("Prompt text is replaced by lengths and hashes.")
        self.summary.configure(text=" ".join(parts))

    def on_save(self) -> None:
        fmt = self.format_var.get()
        if fmt == "markdown":
            ext, text = ".md", export_markdown(
                self.state, self.app.store, self._options())
            payload = text.encode("utf-8")
        else:
            ext = ".json"
            import json
            payload = json.dumps(
                export_json_bundle(self.state, self.app.store),
                indent=1, ensure_ascii=False).encode("utf-8")
        default = f"{self.state.title[:40].replace(' ', '-') or 'run'}{ext}"
        path = filedialog.asksaveasfilename(
            parent=self, initialfile=default,
            defaultextension=ext,
            filetypes=[(ext.strip(".").upper(), f"*{ext}"),
                       ("All files", "*.*")])
        if not path:
            return
        with open(path, "wb") as fh:
            fh.write(payload)
        self.app.live.configure(
            text=f"Exported {os.path.basename(path)} "
                 f"({len(payload):,} bytes)")
