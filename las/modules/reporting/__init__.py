"""Export: Markdown report and JSON bundle (OBS-4, COM-4).

Exports carry provenance (run id, models used, date) and an optional
AI-generated notice that is on by default for Markdown reports (COM-4).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from las.modules.execution import RunState

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


def compare_runs(store, run_a: str, run_b: str) -> dict:
    """Compare two runs (OBS-6): plan diff and output diff.

    Plan diff is by task id (added, removed, changed title/instructions/
    needs); output diff pairs the handoffs of common tasks and reports
    status and token totals. Pure stdlib, no external diff dependency.
    """
    from las.modules.execution import Engine
    engine = Engine(store.data_dir)
    a, b = engine.state(run_a), engine.state(run_b)

    def _task_index(state):
        return {t.id: t for t in state.plan.tasks}

    ia, ib = _task_index(a), _task_index(b)
    added = sorted(set(ib) - set(ia))
    removed = sorted(set(ia) - set(ib))
    changed = []
    for tid in sorted(set(ia) & set(ib)):
        ta, tb = ia[tid], ib[tid]
        diffs = {}
        for field in ("title", "instructions", "needs", "expected_output"):
            if getattr(ta, field) != getattr(tb, field):
                diffs[field] = {"a": getattr(ta, field), "b": getattr(tb, field)}
        if diffs:
            changed.append({"task_id": tid, "fields": diffs})

    constraints_a = {c.id: c.text for c in a.plan.constraints}
    constraints_b = {c.id: c.text for c in b.plan.constraints}
    constraint_diff = {
        "added": sorted(set(constraints_b) - set(constraints_a)),
        "removed": sorted(set(constraints_a) - set(constraints_b)),
        "reworded": sorted(
            cid for cid in set(constraints_a) & set(constraints_b)
            if constraints_a[cid] != constraints_b[cid]),
    }

    outputs = []
    for tid in sorted(set(ia) & set(ib)):
        tsa, tsb = a.tasks.get(tid), b.tasks.get(tid)
        ha = (tsa.handoff or {}).get("summary", "") if tsa else ""
        hb = (tsb.handoff or {}).get("summary", "") if tsb else ""
        outputs.append({
            "task_id": tid,
            "status_a": tsa.state if tsa else "-",
            "status_b": tsb.state if tsb else "-",
            "handoff_a": ha, "handoff_b": hb,
            "handoff_changed": ha != hb,
            "tokens_a": (tsa.usage.get("total_tokens", 0) if tsa and tsa.usage else 0),
            "tokens_b": (tsb.usage.get("total_tokens", 0) if tsb and tsb.usage else 0),
        })
    return {
        "run_a": {"run_id": run_a, "status": a.status, "title": a.title},
        "run_b": {"run_id": run_b, "status": b.status, "title": b.title},
        "plan_diff": {"tasks_added": added, "tasks_removed": removed,
                      "tasks_changed": changed},
        "constraint_diff": constraint_diff,
        "output_diff": outputs,
    }


def render_compare(diff: dict) -> str:
    """Human-readable rendering of compare_runs output."""
    lines = [
        f"run A: {diff['run_a']['run_id']} ({diff['run_a']['status']})",
        f"run B: {diff['run_b']['run_id']} ({diff['run_b']['status']})",
        "",
        "Plan diff:",
    ]
    pd = diff["plan_diff"]
    if not (pd["tasks_added"] or pd["tasks_removed"] or pd["tasks_changed"]):
        lines.append("  (identical task sets)")
    for tid in pd["tasks_added"]:
        lines.append(f"  + {tid} (only in B)")
    for tid in pd["tasks_removed"]:
        lines.append(f"  - {tid} (only in A)")
    for change in pd["tasks_changed"]:
        lines.append(f"  ~ {change['task_id']}: "
                     + ", ".join(sorted(change["fields"])))
    cd = diff["constraint_diff"]
    if cd["added"] or cd["removed"] or cd["reworded"]:
        lines.append("Constraint diff:")
        for cid in cd["added"]:
            lines.append(f"  + {cid} (only in B)")
        for cid in cd["removed"]:
            lines.append(f"  - {cid} (only in A)")
        for cid in cd["reworded"]:
            lines.append(f"  ~ {cid} (reworded)")
    lines.append("")
    lines.append("Output diff (common tasks):")
    for out in diff["output_diff"]:
        marker = "~" if out["handoff_changed"] else "="
        lines.append(
            f"  {marker} {out['task_id']}: {out['status_a']} -> "
            f"{out['status_b']}  "
            + (f"handoff changed ({out['handoff_a'][:40]!r} -> "
               f"{out['handoff_b'][:40]!r})" if out["handoff_changed"]
               else "handoff identical"))
    return "\n".join(lines)


__all__ = ["ExportOptions", "ExportResult", "export_markdown", "export_json_bundle",
           "export_run"]
