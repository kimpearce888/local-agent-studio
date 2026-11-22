"""Handoff schema and validation (spec 6.2, 5.5).

A handoff is the structured result one task passes to the next.  The rules
H-01 .. H-08 make binding constraints travel *verbatim*: constraint text is
compared by SHA-256 of its whitespace-normalized form, never by meaning, and
compaction can never touch these fields (they are pinned in context assembly).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from las.shared.plan_contract import Plan, Task, ValidationIssue

STATUSES = ("done", "partial", "blocked")
MAX_SUMMARY_WORDS = 400
HANDOFF_BLOCK_RE = re.compile(r"<handoff>\s*(.*?)\s*</handoff>", re.DOTALL | re.IGNORECASE)


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces (spec 5.5, H-03)."""
    return " ".join(text.split())


def constraint_hash(text: str) -> str:
    """SHA-256 of the whitespace-normalized constraint text (rule H-03)."""
    return hashlib.sha256(normalize_ws(text).encode("utf-8")).hexdigest()


@dataclass
class KeyFact:
    fact: str
    source: str


@dataclass
class Decision:
    decision: str
    basis: str = ""


@dataclass
class OpenIssue:
    issue: str
    constraint_id: str = ""
    blocking: bool = False


@dataclass
class Deliverable:
    path: str
    sha256: str = ""


@dataclass
class HandoffConstraint:
    """A binding constraint as carried inside a handoff (spec 6.2)."""

    id: str
    text: str
    state: str = "active"
    prerequisite: Optional[str] = None
    authority: str = "user"
    fallback: Optional[str] = None
    resolution_evidence: Optional[dict] = None


@dataclass
class Handoff:
    """The structured result of one task attempt (spec 6.2)."""

    task_id: str
    status: str = "done"
    summary: str = ""
    key_facts: list[KeyFact] = field(default_factory=list)
    binding_constraints: list[HandoffConstraint] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    open_issues: list[OpenIssue] = field(default_factory=list)
    deliverables: list[Deliverable] = field(default_factory=list)
    raw_output_ref: str = ""
    usage: dict = field(default_factory=dict)

    def to_obj(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "key_facts": [{"fact": f.fact, "source": f.source} for f in self.key_facts],
            "binding_constraints": [
                {
                    "id": c.id,
                    "text": c.text,
                    "state": c.state,
                    "prerequisite": c.prerequisite,
                    "authority": c.authority,
                    "fallback": c.fallback,
                    "resolution_evidence": c.resolution_evidence,
                }
                for c in self.binding_constraints
            ],
            "decisions": [{"decision": d.decision, "basis": d.basis} for d in self.decisions],
            "open_issues": [
                {"issue": i.issue, "constraint_id": i.constraint_id, "blocking": i.blocking}
                for i in self.open_issues
            ],
            "deliverables": [{"path": d.path, "sha256": d.sha256} for d in self.deliverables],
            "raw_output_ref": self.raw_output_ref,
            "usage": self.usage,
        }

    def active_constraints(self) -> list[HandoffConstraint]:
        return [c for c in self.binding_constraints if c.state == "active"]

    @classmethod
    def from_obj(cls, obj: dict) -> "Handoff":
        return cls(
            task_id=str(obj.get("task_id", "")),
            status=str(obj.get("status", "")),
            summary=str(obj.get("summary", "")),
            key_facts=[KeyFact(str(f.get("fact", "")), str(f.get("source", "")))
                       for f in obj.get("key_facts", []) if isinstance(f, dict)],
            binding_constraints=[_constraint_from_obj(c)
                                 for c in obj.get("binding_constraints", []) if isinstance(c, dict)],
            decisions=[Decision(str(d.get("decision", "")), str(d.get("basis", "")))
                       for d in obj.get("decisions", []) if isinstance(d, dict)],
            open_issues=[OpenIssue(str(i.get("issue", "")),
                                   str(i.get("constraint_id", "") or ""),
                                   bool(i.get("blocking", False)))
                         for i in obj.get("open_issues", []) if isinstance(i, dict)],
            deliverables=[Deliverable(str(d.get("path", "")), str(d.get("sha256", "") or ""))
                          for d in obj.get("deliverables", []) if isinstance(d, dict)],
            raw_output_ref=str(obj.get("raw_output_ref", "") or ""),
            usage=dict(obj.get("usage") or {}),
        )


def _constraint_from_obj(obj: dict) -> HandoffConstraint:
    return HandoffConstraint(
        id=str(obj.get("id", "")),
        text=str(obj.get("text", "")),
        state=str(obj.get("state", "active")),
        prerequisite=obj.get("prerequisite"),
        authority=str(obj.get("authority", "user")),
        fallback=obj.get("fallback"),
        resolution_evidence=obj.get("resolution_evidence"),
    )


# -- extraction from a model reply ---------------------------------------


def strip_code_fences(text: str) -> str:
    """Remove ``` fences and leading prose around a JSON object or array."""
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z0-9_-]*\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Lenient: keep from the first opening bracket to the last closing one.
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
    if starts and ends and min(starts) < max(ends):
        text = text[min(starts):max(ends) + 1]
    return text


def extract_handoff(final_text: str) -> Optional[Handoff]:
    """Extract and parse the ``<handoff>`` block from a final message.

    Returns None when no block is present or the JSON does not parse
    (rule H-01).
    """
    match = HANDOFF_BLOCK_RE.search(final_text)
    if not match:
        return None
    try:
        obj = json.loads(strip_code_fences(match.group(1)))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return Handoff.from_obj(obj)


# -- validation (spec 5.5) ------------------------------------------------


@dataclass
class EvidenceContext:
    """What the engine knows when checking resolution evidence (H-05).

    The model's own claim is never enough: a constraint may move to
    ``resolved`` only with evidence from its named authority.
    """

    attempt_tool_calls: set[str] = field(default_factory=set)   # tool names called this attempt
    succeeded_tasks: set[str] = field(default_factory=set)      # task ids that succeeded
    user_approvals: set[str] = field(default_factory=set)       # task ids approved at a gate


def validate_handoff(
    handoff: Handoff | None,
    task: Task,
    plan: Plan,
    workspace=None,
    evidence: EvidenceContext | None = None,
) -> list[ValidationIssue]:
    """Run rules H-01 .. H-08 against a handoff.

    ``workspace`` (a las.modules.security.Workspace) enables the deliverable hash
    check H-07; without it the check is structural only.
    """
    issues: list[ValidationIssue] = []
    evidence = evidence or EvidenceContext()

    # H-01: the block matches the handoff schema
    if handoff is None:
        issues.append(ValidationIssue("H-01", "The result had no <handoff> block.", task.id))
        return issues
    if handoff.task_id != task.id:
        issues.append(ValidationIssue(
            "H-01", f"The handoff names task {handoff.task_id!r}; expected {task.id!r}.", task.id))

    # H-02: status
    if handoff.status not in STATUSES:
        issues.append(ValidationIssue(
            "H-02", f"The handoff status {handoff.status!r} is not one of done, partial, blocked.",
            task.id))
    elif handoff.status == "blocked" and not any(
            i.blocking for i in handoff.open_issues):
        # The four-field blocker (spec 5.5): a blocked stop status must
        # name its blocker - a blocking open issue carrying its
        # constraint id and prerequisite - or it is ownership deferral.
        issues.append(ValidationIssue(
            "H-02", "A blocked handoff must name a blocking open issue "
            "(with its constraint_id) as its blocker.", task.id))

    # H-08: summary length
    if len(handoff.summary.split()) > MAX_SUMMARY_WORDS:
        issues.append(ValidationIssue(
            "H-08", f"The summary is longer than {MAX_SUMMARY_WORDS} words.", task.id))

    # The constraint set this task was given in its context block.
    required = {c.id: c for c in plan.task_constraints(task)}
    given = {c.id: c for c in handoff.binding_constraints}

    # H-03: every required constraint appears, verbatim by hash
    for cid, plan_c in required.items():
        if cid not in given:
            issues.append(ValidationIssue(
                "H-03", f"The handoff dropped constraint {cid}.", task.id, cid))
            continue
        if constraint_hash(_strip_rendered_decoration(
                given[cid].text, plan_c)) != constraint_hash(plan_c.text):
            issues.append(ValidationIssue(
                "H-03", f"Constraint {cid} was reworded; it must be copied verbatim.", task.id, cid))

    # duplicate ids inside the handoff
    ids = [c.id for c in handoff.binding_constraints]
    for cid in set(ids):
        if ids.count(cid) > 1:
            issues.append(ValidationIssue("H-04", f"Constraint {cid} appears twice.", task.id, cid))

    # H-04: a *new* constraint may not reuse an existing id
    all_plan_ids = {c.id for c in plan.constraints}
    for cid, hc in given.items():
        if cid not in required and cid in all_plan_ids:
            issues.append(ValidationIssue(
                "H-04", f"Constraint {cid} reuses an existing id but is not the same constraint.",
                task.id, cid))

    # H-05: resolved only with evidence from the named authority
    for hc in handoff.binding_constraints:
        if hc.state != "resolved":
            continue
        base = hc.id if hc.id in required else cid_of(plan, hc.id)
        authority = hc.authority or (required[base].authority if base in required else "user")
        ev = hc.resolution_evidence
        if not isinstance(ev, dict) or not ev.get("source"):
            issues.append(ValidationIssue(
                "H-05", f"Constraint {hc.id} is resolved without evidence.", task.id, hc.id))
            continue
        source = str(ev.get("source", ""))
        ref = str(ev.get("ref", ""))
        if authority.startswith("task:") and source == authority and ref in evidence.succeeded_tasks:
            continue
        if authority.startswith("tool:") and source == authority and authority.split(":", 1)[1] in evidence.attempt_tool_calls:
            continue
        if authority == "user" and source == "user" and ref in evidence.user_approvals:
            continue
        # Also accept a verification task or tool that the constraint itself names
        # even when authority was recorded as user (planner shorthand).
        if source.startswith("task:") and ref in evidence.succeeded_tasks:
            continue
        if source.startswith("tool:") and source.split(":", 1)[1] in evidence.attempt_tool_calls:
            continue
        issues.append(ValidationIssue(
            "H-05",
            f"Constraint {hc.id} is resolved on the model's own claim; evidence from "
            f"{authority or 'its authority'} is missing.", task.id, hc.id))

    # H-06: every key fact cites a source
    for f in handoff.key_facts:
        if not f.fact.strip():
            issues.append(ValidationIssue("H-06", "A key fact is empty.", task.id))
        if not _source_ok(f.source, workspace):
            issues.append(ValidationIssue(
                "H-06", f"The fact {f.fact[:60]!r} has no source (message:, tool: or a file path).",
                task.id))

    # open issues: a blocking issue names its constraint
    for i in handoff.open_issues:
        if i.blocking and not i.constraint_id:
            issues.append(ValidationIssue(
                "H-06", "A blocking open issue does not name its constraint.", task.id))

    # H-07: deliverables exist and their hashes match
    for d in handoff.deliverables:
        if not d.path:
            issues.append(ValidationIssue("H-07", "A deliverable has no path.", task.id))
            continue
        if workspace is None:
            if d.sha256 and not re.fullmatch(r"[0-9a-f]{64}", d.sha256):
                issues.append(ValidationIssue(
                    "H-07", f"The deliverable {d.path} has a malformed hash.", task.id))
            continue
        if not workspace.exists(d.path):
            issues.append(ValidationIssue(
                "H-07", f"The deliverable {d.path} does not exist in the workspace.", task.id))
            continue
        actual = workspace.sha256(d.path)
        if d.sha256 and d.sha256 != actual:
            issues.append(ValidationIssue(
                "H-07", f"The deliverable {d.path} does not match its hash.", task.id))

    return issues


def cid_of(plan: Plan, constraint_id: str) -> str:
    return constraint_id


# The context renderer (context._constraint_line) decorates each
# constraint with a [id] (state: ...) header, a "text:" line and
# prerequisite/authority/fallback sub-lines.  A model that copies the
# rendered block verbatim includes that decoration; H-03 must not fail
# it for copying what the engine itself rendered.  Strip exactly the
# engine's decoration - nothing else; any other change still fails.
_HEADER_RE_CACHE: dict = {}


def _strip_rendered_decoration(text: str, ref) -> str:
    """Recover the bare constraint text from a copy of the rendered
    context block: the [id] (state: ...) header, a ``text:`` prefix and
    surrounding quotes, and trailing prerequisite/authority/fallback
    sub-lines whose values equal the plan's own fields."""
    lines = text.split("\n")
    rx = _HEADER_RE_CACHE.get(ref.id)
    if rx is None:
        rx = re.compile(r"^\s*\[" + re.escape(ref.id)
                        + r"\]\s*\(state:\s*\w+\)\s*")
        _HEADER_RE_CACHE[ref.id] = rx
    if lines and rx.match(lines[0]):
        lines[0] = rx.sub("", lines[0])
        if not lines[0].strip():
            lines.pop(0)
    if lines:
        first = lines[0].strip()
        m = re.match(r'^text:\s*"(.*)"\s*$', first, re.DOTALL)
        if m:
            lines[0] = m.group(1)
        elif first.startswith("text:"):
            lines[0] = first[len("text:"):].strip().strip('"')
    fields = []
    if getattr(ref, "prerequisite", None):
        fields.append(f"prerequisite: {ref.prerequisite}")
    if getattr(ref, "authority", None):
        fields.append(f"authority: {ref.authority}")
    if getattr(ref, "fallback", None):
        fields.append(f"fallback while unresolved: {ref.fallback}")
    while lines:
        last = lines[-1].strip()
        if last in fields:
            lines.pop()
        else:
            break
    return "\n".join(lines)


def _source_ok(source: str, workspace) -> bool:
    if not source or not source.strip():
        return False
    s = source.strip()
    if s.startswith(("message:", "tool:", "file:", "user:")):
        return len(s.split(":", 1)[1]) > 0
    if workspace is not None:
        try:
            return workspace.exists(s)
        except Exception:
            return False
    # A bare relative path with no spaces is accepted structurally.
    return bool(s) and " " not in s and not s.startswith("/")



__all__ = [
    "STATUSES",
    "MAX_SUMMARY_WORDS",
    "HANDOFF_BLOCK_RE",
    "normalize_ws",
    "constraint_hash",
    "KeyFact",
    "Decision",
    "OpenIssue",
    "Deliverable",
    "HandoffConstraint",
    "Handoff",
    "strip_code_fences",
    "extract_handoff",
    "EvidenceContext",
    "validate_handoff",
]
