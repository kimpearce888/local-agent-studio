"""The planner contract (spec 5.1, PLN-1..PLN-5).

The planner runs as its own session with a fixed, versioned system prompt.
Its output is one JSON object that conforms to plan schema v1; the validation
rules of spec 5.2 run on every reply, and a repair loop of at most three
rounds asks for corrected JSON only.  After the third failure the engine
returns the longest valid prefix and marks the first failing task.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from las.shared.provider_contract import Adapter, ChatRequest, Message, ProviderError
from las.modules.handoffs import strip_code_fences
from las.modules.planquality import validate_plan
from las.shared.plan_contract import (
    Constraint, GRANULARITY_TASK_COUNTS, DEFAULT_TOOLS, Plan, PlanFormatError, Task,
    ValidationIssue,
)
from las.shared.tool_catalog import KNOWN_TOOLS

PLANNER_SYSTEM_PROMPT = """You are the planner for Local Agent Studio. Split the user's goal into a chain of small tasks that other agents will run one at a time, each in its own fresh session.

Rules:
1. Split the goal into tasks that one agent can finish in one session within the stated context length.
2. Make each task self-contained: it sees only the goal, the binding constraints and the results of tasks listed in needs.
3. Dependencies may name only earlier tasks.
4. Copy every hard requirement in the goal (words such as must, never, only, before, requires approval) into constraints exactly as written, one per requirement, and add a prerequisite, an authority and a fallback where the goal implies them.
5. Add a collector task whenever results from several tasks must be combined, and a final task that produces the deliverable.
6. Reply with JSON only."""

MAX_REPAIR_ROUNDS = 3

PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "prerequisite": {"type": "string"},
                    "authority": {"type": "string"},
                    "fallback": {"type": "string"},
                    "scope": {"type": ["array", "string"]},
                },
                "required": ["id", "text"],
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "title": {"type": "string"},
                    "instructions": {"type": "string"},
                    "needs": {"type": "array", "items": {"type": "string"}},
                    "expected_output": {"type": "string"},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "role": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "instructions", "needs"],
            },
        },
    },
    "required": ["title", "tasks", "constraints"],
}

SCHEMA_AS_TEXT = (
    "Reply with JSON only, exactly this shape:\n"
    '{"title": "...", "constraints": [{"id": "c1", "text": "<copied verbatim from the goal>",'
    ' "prerequisite": "...", "authority": "user", "fallback": "...", "scope": "all"}],'
    ' "tasks": [{"id": "t1", "type": "task|collector", "title": "...", "instructions": "...",'
    ' "needs": ["t0"], "expected_output": "...", "constraints": ["c1"], "role": "worker",'
    ' "tools": ["list_files", "read_file", "write_file", "search_files", "patch_file"]}]}'
)

HARD_REQUIREMENT_RE = re.compile(
    r"[^.!?\n]*\b(must|never|only|requires approval|requires approval to|shall not|do not)\b[^.!?\n]*[.!?]?",
    re.IGNORECASE)


def extract_constraint_candidates(goal: str) -> list[str]:
    """Sentences of the goal that carry hard requirements (PLN-4).

    Used to check the planner carried them all; the planner itself decides
    what becomes a constraint.
    """
    seen: list[str] = []
    for m in HARD_REQUIREMENT_RE.finditer(goal):
        text = m.group(0).strip()
        if len(text) >= 8 and text not in seen:
            seen.append(text)
    return seen


def find_source_span(goal: str, text: str) -> Optional[list[int]]:
    """Character range of ``text`` in the goal (verbatim copy check, AC-03)."""
    norm_goal = " ".join(goal.split())
    norm_text = " ".join(text.split())
    idx = norm_goal.find(norm_text)
    if idx == -1:
        return None
    return [idx, idx + len(norm_text)]


@dataclass
class PlanResult:
    plan: Optional[Plan]
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    repair_rounds: int = 0
    partial: bool = False
    raw_replies: list[str] = field(default_factory=list)
    critique: list[dict] = field(default_factory=list)
    error: Optional[str] = None


def _planner_user_message(goal: str, options: dict) -> str:
    granularity = options.get("granularity", "normal")
    lo, hi = GRANULARITY_TASK_COUNTS.get(granularity, (6, 15))
    max_tasks = options.get("max_tasks", 30)
    parts = [f"GOAL:\n{goal}", "",
             f"Planning options: granularity {granularity} (about {lo} to {hi} tasks), "
             f"at most {max_tasks} tasks."]
    if options.get("stage_barriers"):
        parts.append("Add a collector task after each stage.")
    parts.append(SCHEMA_AS_TEXT)
    return "\n".join(parts)


def _repair_message(issues: list[ValidationIssue]) -> str:
    lines = ["Your reply did not pass validation. Problems:"]
    for issue in issues:
        lines.append(f"- {issue.rule}: {issue.message}")
    lines.append("Reply with the corrected JSON only. Keep every task and constraint id stable.")
    return "\n".join(lines)


def _collect_text(events) -> str:
    text = ""
    for ev in events:
        if ev.kind == "text_delta":
            text += ev.text
        elif ev.kind == "error" and ev.error is not None:
            raise ev.error
    return text


class Planner:
    """Runs the planner session with the repair loop (spec 5.1)."""

    def __init__(self, adapter: Adapter, model: str, params: Optional[dict] = None,
                 json_schema_supported: bool = True):
        self.adapter = adapter
        self.model = model
        self.params = params or {"temperature": 0.2, "max_tokens": 4096}
        self.json_schema_supported = json_schema_supported

    def plan(self, goal: str, options: Optional[dict] = None) -> PlanResult:
        options = options or {}
        request = self._request(goal, options)
        history: list[Message] = list(request.messages)
        result = PlanResult(plan=None, valid=False)

        try:
            reply = _collect_text(self.adapter.chat(request))
        except ProviderError as exc:
            result.error = exc.code
            return result
        result.raw_replies.append(reply)

        for round_no in range(MAX_REPAIR_ROUNDS + 1):
            draft = _parse_reply(reply)
            if draft is None:
                result.issues = [ValidationIssue(
                    "V-01", "The planner reply is not JSON in the expected format.")]
            else:
                plan = _plan_from_draft(draft, goal, options)
                issues = validate_plan(plan, max_tasks=options.get("max_tasks", 30),
                                       known_tools=KNOWN_TOOLS)
                if not issues:
                    result.plan = plan
                    result.valid = True
                    result.issues = []
                    return result
                result.issues = issues
                result.plan = plan
            if round_no == MAX_REPAIR_ROUNDS:
                break
            # Repair round: original request + invalid reply + rule list.
            history = history + [Message("assistant", reply),
                                 Message("user", _repair_message(result.issues))]
            repair_request = ChatRequest(
                model=self.model, messages=history, json_schema=(
                    PLAN_JSON_SCHEMA if self.json_schema_supported else None),
                params=dict(self.params))
            try:
                reply = _collect_text(self.adapter.chat(repair_request))
            except ProviderError as exc:
                result.error = exc.code
                break
            result.raw_replies.append(reply)
            result.repair_rounds = round_no + 1

        # Longest valid prefix of the plan, first failing task marked (spec 5.1).
        if result.plan is not None:
            prefix, first_bad = _longest_valid_prefix(result.plan, result.issues)
            result.plan = prefix
            result.partial = True
            if first_bad:
                result.issues.append(ValidationIssue(
                    "V-01", f"The plan was truncated at task {first_bad}; fix it in the editor.",
                    first_bad))
        return result

    def critique(self, goal: str, plan: Plan) -> list[dict]:
        """Optional critique pass (spec 5.1): at most five issues, suggestions only."""
        request = ChatRequest(
            model=self.model,
            messages=[
                Message("system",
                        "You review a task plan. Reply with JSON only: an array of at most "
                        "five objects {\"issue\": \"missing step|oversized task|ambiguous "
                        "instruction|dropped constraint|missing collector\", \"task_id\": "
                        "\"...\", \"note\": \"...\"}. If the plan is good, reply []."),
                Message("user", "GOAL:\n" + goal + "\n\nPLAN:\n" + json.dumps(
                    plan.to_obj(), ensure_ascii=False, indent=1)),
            ],
            params=dict(self.params))
        try:
            reply = _collect_text(self.adapter.chat(request))
        except ProviderError:
            return []
        try:
            parsed = json.loads(strip_code_fences(reply))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)][:5]

    def _request(self, goal: str, options: dict) -> ChatRequest:
        return ChatRequest(
            model=self.model,
            messages=[Message("system", PLANNER_SYSTEM_PROMPT),
                      Message("user", _planner_user_message(goal, options))],
            json_schema=PLAN_JSON_SCHEMA if self.json_schema_supported else None,
            params=dict(self.params))


def _parse_reply(reply: str) -> Optional[dict]:
    try:
        obj = json.loads(strip_code_fences(reply))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _plan_from_draft(draft: dict, goal: str, options: dict) -> Plan:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = Plan(
        id=f"plan-{secrets.token_hex(4)}",
        title=str(draft.get("title") or goal[:60]),
        goal=goal,
        created_at=now,
        options={
            "granularity": options.get("granularity", "normal"),
            "max_tasks": int(options.get("max_tasks", 30)),
            "stage_barriers": bool(options.get("stage_barriers", False)),
        },
    )
    for c in draft.get("constraints", []) or []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text", ""))
        span = find_source_span(goal, text)
        plan.constraints.append(Constraint(
            id=str(c.get("id", "")),
            text=text,
            state="active",
            prerequisite=c.get("prerequisite"),
            authority=str(c.get("authority", "user")),
            fallback=c.get("fallback"),
            scope=c.get("scope", "all"),
            origin="goal" if span is not None else "planner",
            source_span=span,
        ))
    for t in draft.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        task_type = str(t.get("type", "task"))
        role = t.get("role")
        if role is None:
            role = "collector" if task_type == "collector" else "worker"
        tools = t.get("tools")
        if tools is None:
            tools = list(DEFAULT_TOOLS)
        plan.tasks.append(Task(
            id=str(t.get("id", "")),
            type=task_type,
            title=str(t.get("title", "")),
            instructions=str(t.get("instructions", "")),
            needs=[str(n) for n in (t.get("needs") or [])],
            expected_output=str(t.get("expected_output", "") or ""),
            constraints=[str(c) for c in (t.get("constraints") or [])],
            role=str(role),
            tools=[str(x) for x in tools],
        ))
    return plan


def _longest_valid_prefix(plan: Plan, issues: list[ValidationIssue]) -> tuple[Plan, Optional[str]]:
    """Keep the longest prefix of tasks that pass the per-task rules."""
    from las.shared.plan_contract import (
        INSTRUCTIONS_MAX, INSTRUCTIONS_MIN, MAX_NEEDS, MAX_TASK_CONSTRAINTS, TASK_ID_RE, TITLE_MAX,
    )
    kept: list[Task] = []
    kept_ids: set[str] = set()
    first_bad: Optional[str] = None
    for t in plan.tasks:
        per_task_ok = (
            TASK_ID_RE.match(t.id) is not None
            and t.id not in kept_ids
            and 1 <= len(t.title) <= TITLE_MAX
            and (t.type in ("gate", "note") or INSTRUCTIONS_MIN <= len(t.instructions) <= INSTRUCTIONS_MAX)
            and len(t.needs) <= MAX_NEEDS
            and len(t.constraints) <= MAX_TASK_CONSTRAINTS
            and all(tool in KNOWN_TOOLS for tool in t.tools)
        )
        deps_ok = all(dep in kept_ids for dep in t.needs)
        if per_task_ok and deps_ok:
            kept.append(t)
            kept_ids.add(t.id)
        else:
            first_bad = t.id
            break
    prefix = Plan(
        id=plan.id, title=plan.title, goal=plan.goal, created_at=plan.created_at,
        options=plan.options, constraints=[], tasks=kept)
    for c in plan.constraints:
        if not c.text.strip() or len(c.text) > 1000:
            continue
        if isinstance(c.scope, list):
            trimmed = [tid for tid in c.scope if tid in kept_ids]
            if not trimmed:
                continue
            c.scope = trimmed
        prefix.constraints.append(c)
    return prefix, first_bad



__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "MAX_REPAIR_ROUNDS",
    "PLAN_JSON_SCHEMA",
    "SCHEMA_AS_TEXT",
    "extract_constraint_candidates",
    "find_source_span",
    "PlanResult",
    "Planner",
]
