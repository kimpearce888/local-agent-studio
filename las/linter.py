"""Plan linting (spec 5.3, BLD-7).

Rules L-01 .. L-09 with the exact messages from the specification.  L-01 is
the Rule of Two gate check (BLD-4): a task with all three risk flags and no
approval gate is an *error* that blocks the run.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass

from las.context import AssembledContext, assemble_context
from las.schema.handoff import Handoff
from las.schema.plan import Plan
from las.tools import PermissionBroker
from las.workspace import Workspace

LONG_INSTRUCTIONS = 2500
CONTEXT_WARNING = 0.80
DEFAULT_HANDOFF_WORDS = 120


@dataclass
class Finding:
    rule: str
    severity: str                  # error | warning | info | suggestion
    message: str
    task_id: str = ""

    def to_obj(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "task_id": self.task_id}


def _placeholder_handoff(task) -> Handoff:
    words = (task.expected_output or ("placeholder " * DEFAULT_HANDOFF_WORDS)).split()
    return Handoff(task_id=task.id, status="done", summary=" ".join(words[:400]))


def _predict(plan: Plan, context_length: int) -> dict[str, AssembledContext]:
    """Assemble a predicted context per task with placeholder handoffs."""
    predicted: dict[str, AssembledContext] = {}
    with tempfile.TemporaryDirectory(prefix="las-lint-") as tmp:
        workspace = Workspace(tmp)
        broker = PermissionBroker(workspace)
        handoffs = {t.id: _placeholder_handoff(t) for t in plan.tasks}
        for t in plan.tasks:
            try:
                predicted[t.id] = assemble_context(
                    t, plan, handoffs, workspace, broker,
                    context_length=context_length)
            except Exception:
                continue
    return predicted


def lint(
    plan: Plan,
    context_length: int = 8192,
    provider_reachable: dict[str, bool] | None = None,
    default_provider_id: str = "",
    tool_calling: bool = True,
) -> list[Finding]:
    """Run rules L-01 .. L-09.  Errors block the run (spec S4 preflight)."""
    findings: list[Finding] = []
    reachable = provider_reachable or {}

    with tempfile.TemporaryDirectory(prefix="las-lint-") as tmp:
        broker = PermissionBroker(Workspace(tmp))

        # Rule of Two flags, computed by code in plan order.
        flags: dict[str, tuple[bool, bool, bool]] = {}
        for t in plan.tasks:
            flags[t.id] = broker.compute_flags(t, plan, flags)

    needed_by: set[str] = set()
    for t in plan.tasks:
        needed_by.update(t.needs)

    predicted = _predict(plan, context_length)

    for t in plan.tasks:
        a, b, c = flags.get(t.id, (False, False, False))
        # L-01: three of three flags without a gate is an error.
        if a and b and c and not t.gate_before:
            findings.append(Finding(
                "L-01", "error",
                f"{t.id} can read untrusted text, reach private files and send data out. "
                "Add an approval gate before it.", t.id))
        # L-02 / L-03: predicted context fit.
        ctx = predicted.get(t.id)
        if ctx is not None and ctx.available_input > 0:
            if ctx.overflow:
                findings.append(Finding(
                    "L-02", "error",
                    f"{t.id} will not fit in the model's context, even after compaction. "
                    "Split the task.", t.id))
            elif ctx.token_estimate > ctx.available_input * CONTEXT_WARNING:
                pct = int(ctx.token_estimate * 100 / ctx.available_input)
                findings.append(Finding(
                    "L-03", "warning", f"{t.id} uses {pct}% of the context.", t.id))
        # L-04: unused result.
        if t.id not in needed_by and t is not plan.tasks[-1]:
            findings.append(Finding(
                "L-04", "warning", f"{t.id}'s result is not used by any later task.", t.id))
        # L-06: long instructions.
        if len(t.instructions) > LONG_INSTRUCTIONS:
            findings.append(Finding(
                "L-06", "warning", f"{t.id} is long. Consider splitting it.", t.id))
        # L-07: tools enabled but the model cannot call tools.
        if t.tools and not tool_calling:
            findings.append(Finding(
                "L-07", "warning",
                "This model cannot call tools; the text tool protocol will be used.", t.id))
        # L-08: a collector with fewer than two inputs.
        if t.type == "collector" and len([d for d in t.needs]) < 2:
            findings.append(Finding(
                "L-08", "info", f"{t.id} is a collector with one input.", t.id))
        # L-09: provider reachability.
        provider_id = ""
        if t.model and t.model.get("provider_id"):
            provider_id = str(t.model["provider_id"])
        elif default_provider_id:
            provider_id = default_provider_id
        if provider_id and provider_id in reachable and not reachable[provider_id]:
            findings.append(Finding(
                "L-09", "error", f"The server for {t.id}'s model is not reachable.", t.id))

    # L-05: a constraint no task carries.  A constraint is carried when a
    # task lists it or its scope covers at least one task ("all" covers all).
    carried_ids = {cid for t in plan.tasks for cid in t.constraints}
    for c in plan.constraints:
        covered = c.id in carried_ids or c.scope == "all" or (
            isinstance(c.scope, list) and len(c.scope) > 0)
        if not covered:
            findings.append(Finding(
                "L-05", "warning", f"Constraint {c.id} is not carried by any task."))

    # Suggestions from the critique pass arrive as pre-made findings.
    return findings


def add_suggestions(findings: list[Finding], critique: list[dict]) -> list[Finding]:
    """Fold planner critique output into the lint list (spec 5.1)."""
    for item in critique[:5]:
        findings.append(Finding(
            "CRIT", "suggestion",
            f"Suggestion ({item.get('issue', 'note')}): {item.get('note', '')}",
            str(item.get("task_id", ""))))
    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]
