"""Export: Markdown report and JSON bundle (OBS-4, COM-4).

Exports carry provenance (run id, models used, date) and an optional
AI-generated notice that is on by default for Markdown reports (COM-4).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from las.runstate import RunState

AI_NOTICE = ("This document was generated with the assistance of AI (Local Agent "
             "Studio). Verify important details before use.")


@dataclass
class ExportOptions:
    include_transcripts: bool = True
    include_plan: bool = True
    include_workspace_files: bool = False
    ai_notice: bool = True
    redact_prompts: bool = False


@dataclass
class ExportResult:
    format: str
    path: str = ""
    content: str = ""
    size_bytes: int = 0


def export_markdown(state: RunState, store, options: ExportOptions | None = None) -> str:
    """Render a run as a Markdown report."""
    options = options or ExportOptions()
    lines: list[str] = []
    plan = state.plan
    lines.append(f"# {state.title or plan.title}")
    lines.append("")
    lines.append(f"- Run id: `{state.run_id}`")
    lines.append(f"- Status: {state.status}")
    models = sorted({a.model for t in state.tasks.values() for a in t.attempts if a.model})
    if models:
        lines.append(f"- Models used: {', '.join(f'`{m}`' for m in models)}")
    lines.append(f"- Tokens used: {state.tokens_used()}")
    lines.append(f"- Tasks: {sum(1 for t in state.tasks.values() if t.state == 'succeeded')}"
                 f" of {len(plan.tasks)} succeeded")
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(plan.goal)
    lines.append("")

    if options.include_plan:
        lines.append("## Plan")
        lines.append("")
        for t in plan.tasks:
            gate = " (approval gate before)" if t.gate_before else ""
            needs = ", ".join(t.needs) if t.needs else "-"
            lines.append(f"### {t.id}: {t.title} `{t.type}`{gate}")
            lines.append("")
            lines.append(f"- Needs: {needs}")
            lines.append(f"- Role: {t.role}")
            if t.constraints:
                lines.append(f"- Constraints: {', '.join(t.constraints)}")
            lines.append("")
            if not options.redact_prompts:
                lines.append(t.instructions)
                lines.append("")

    lines.append("## Results")
    lines.append("")
    for t in plan.tasks:
        ts = state.tasks.get(t.id)
        if ts is None:
            continue
        lines.append(f"### {t.id}: {t.title} — {ts.state}")
        lines.append("")
        if ts.state == "succeeded" and ts.handoff:
            h = ts.handoff
            lines.append(f"**Status:** {h.get('status', '')}")
            lines.append("")
            lines.append(h.get("summary", ""))
            lines.append("")
            facts = h.get("key_facts", [])
            if facts:
                lines.append("Key facts:")
                for f in facts:
                    lines.append(f"- {f.get('fact', '')} (source: {f.get('source', '')})")
                lines.append("")
            deliverables = h.get("deliverables", [])
            if deliverables:
                lines.append("Deliverables:")
                for d in deliverables:
                    lines.append(f"- `{d.get('path', '')}` (sha256 `{d.get('sha256', '')[:16]}…`)")
                lines.append("")
        elif ts.error_code:
            lines.append(f"Failed with `{ts.error_code}`.")
            lines.append("")

    if options.include_transcripts:
        lines.append("## Transcripts")
        lines.append("")
        for t in plan.tasks:
            ts = state.tasks.get(t.id)
            if ts is None:
                continue
            for a in ts.attempts:
                d = store.attempt_dir(state.run_id, t.id, a.attempt_id)
                final = os.path.join(d, "final.txt")
                if os.path.exists(final):
                    lines.append(f"### {t.id} / {a.attempt_id} ({a.status})")
                    lines.append("")
                    if options.redact_prompts:
                        lines.append(f"*(final message redacted, {os.path.getsize(final)} bytes)*")
                    else:
                        with open(final, encoding="utf-8") as fh:
                            lines.append(fh.read().strip())
                    lines.append("")

    if options.ai_notice:
        lines.append("---")
        lines.append("")
        lines.append(f"> {AI_NOTICE} Run `{state.run_id}`, "
                     f"models: {', '.join(models) if models else 'n/a'}.")
        lines.append("")
    return "\n".join(lines)


def export_json_bundle(state: RunState, store) -> dict:
    """A re-importable JSON bundle: plan, handoffs and the event log."""
    events = [e.to_obj() for e in store.events(state.run_id)]
    return {
        "schema_version": 1,
        "run": state.to_obj(),
        "plan": state.plan.to_obj(),
        "events": events,
        "ai_generated": True,
    }


def export_run(state: RunState, store, fmt: str = "markdown",
               options: ExportOptions | None = None, output_path: str | None = None) -> ExportResult:
    options = options or ExportOptions()
    if fmt == "json":
        bundle = export_json_bundle(state, store)
        content = json.dumps(bundle, ensure_ascii=False, indent=2)
    else:
        content = export_markdown(state, store, options)
    result = ExportResult(format=fmt, content=content, size_bytes=len(content.encode("utf-8")))
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        result.path = output_path
    return result


__all__ = ["ExportOptions", "ExportResult", "export_markdown", "export_json_bundle",
           "export_run"]
