"""Plan validation rules V-02..V-12 (spec 5.2).

The plan schema v1 types, constants and parsing moved to the shared kernel
(``las.shared.plan_contract``, ADR-0001); the validation rules are business
logic and stay here until the modular migration moves them into the
planquality module (ADR-0004). This module re-exports the kernel names so
existing imports keep working.
"""

from __future__ import annotations

from typing import Any

from las.shared.plan_contract import (
    CONSTRAINT_TEXT_MAX, DEFAULT_MAX_TASKS, DEFAULT_TOOLS, GRANULARITIES,
    GRANULARITY_TASK_COUNTS, INSTRUCTIONS_MAX, INSTRUCTIONS_MIN, MAX_NEEDS,
    MAX_TASKS_HARD, MAX_TASK_CONSTRAINTS, ROLES, SCHEMA_VERSION, TASK_ID_RE,
    TASK_TYPES, TITLE_MAX, VARIABLE_RE, Constraint, Plan, PlanFormatError, Task,
    ValidationIssue, _find_cycle, _variables_in_task, plan_constraints_ids,
    plan_variables, substitute_variables,
)

__all__ = [
    "SCHEMA_VERSION", "TASK_TYPES", "ROLES", "GRANULARITIES", "GRANULARITY_TASK_COUNTS",
    "DEFAULT_TOOLS", "MAX_TASKS_HARD", "DEFAULT_MAX_TASKS", "MAX_NEEDS",
    "MAX_TASK_CONSTRAINTS", "TITLE_MAX", "INSTRUCTIONS_MIN", "INSTRUCTIONS_MAX",
    "CONSTRAINT_TEXT_MAX", "TASK_ID_RE", "VARIABLE_RE",
    "PlanFormatError", "ValidationIssue", "Constraint", "Task", "Plan",
    "validate_plan", "plan_constraints_ids", "plan_variables", "substitute_variables",
]


# -- validation (spec 5.2) ------------------------------------------------


def validate_plan(
    plan: Plan,
    max_tasks: int | None = None,
    known_tools: set[str] | None = None,
    variables: dict[str, str] | None = None,
) -> list[ValidationIssue]:
    """Run rules V-02 .. V-12 (V-01 is enforced by Plan.from_obj).

    ``known_tools`` defaults to the built-in tool registry.  ``variables``
    supplies values for ``{{placeholders}}``; a variable missing from the map
    is only an error when it has no default in the plan options.
    """
    issues: list[ValidationIssue] = []
    limit = max_tasks if max_tasks is not None else int(plan.options.get("max_tasks", DEFAULT_MAX_TASKS))
    limit = min(limit, MAX_TASKS_HARD)
    tools = known_tools if known_tools is not None else set(DEFAULT_TOOLS) | {"append_file"}
    variables = variables or {}

    # V-02: unique ids matching the pattern
    seen: set[str] = set()
    for t in plan.tasks:
        if not TASK_ID_RE.match(t.id):
            issues.append(ValidationIssue(
                "V-02", f"Task id {t.id} is not a valid id (lowercase letters, digits, - or _).", t.id))
        if t.id in seen:
            issues.append(ValidationIssue("V-02", f"Task id {t.id} is used twice.", t.id))
        seen.add(t.id)

    index = {t.id: i for i, t in enumerate(plan.tasks)}

    for t in plan.tasks:
        # V-03: needs name only earlier tasks
        for dep in t.needs:
            if dep not in index:
                issues.append(ValidationIssue(
                    "V-03", f"{t.id} needs {dep}, which does not exist.", t.id))
            elif index[dep] >= index[t.id]:
                issues.append(ValidationIssue(
                    "V-03", f"{t.id} needs {dep}, which comes later.", t.id))
        # V-05: title and instruction lengths
        if not (1 <= len(t.title) <= TITLE_MAX):
            issues.append(ValidationIssue(
                "V-05", f"{t.id} has no title or it is longer than {TITLE_MAX} characters.", t.id))
        if t.type not in ("gate", "note"):
            if not (INSTRUCTIONS_MIN <= len(t.instructions) <= INSTRUCTIONS_MAX):
                if not t.instructions.strip():
                    issues.append(ValidationIssue("V-05", f"{t.id} has no instructions.", t.id))
                else:
                    issues.append(ValidationIssue(
                        "V-05",
                        f"{t.id} has instructions shorter than {INSTRUCTIONS_MIN} or longer "
                        f"than {INSTRUCTIONS_MAX} characters.", t.id))
        # V-07: at most 8 needs and 12 constraints per task
        if len(t.needs) > MAX_NEEDS:
            issues.append(ValidationIssue("V-07", f"{t.id} lists more than {MAX_NEEDS} inputs.", t.id))
        if len(t.constraints) > MAX_TASK_CONSTRAINTS:
            issues.append(ValidationIssue(
                "V-07", f"{t.id} lists more than {MAX_TASK_CONSTRAINTS} constraints.", t.id))
        # V-11: tools exist
        for tool in t.tools:
            if tool not in tools:
                issues.append(ValidationIssue(
                    "V-11", f"{t.id} asks for a tool that does not exist: {tool}.", t.id))
        # V-12: variables have values
        for name in _variables_in_task(t):
            if name not in variables:
                issues.append(ValidationIssue(
                    "V-12", f"The variable {name} has no value.", t.id))

    # V-04: no cycles (recheck on import; V-03 makes them impossible in order)
    cycle = _find_cycle(plan)
    if cycle:
        issues.append(ValidationIssue("V-04", "The plan contains a loop: " + ", ".join(cycle) + "."))

    # V-06: task count
    if not (1 <= len(plan.tasks) <= limit):
        if len(plan.tasks) > limit:
            issues.append(ValidationIssue(
                "V-06", f"The plan has {len(plan.tasks)} tasks; the limit is {limit}."))
        else:
            issues.append(ValidationIssue("V-06", "The plan has no tasks."))

    # V-08: the last task is the only task no other task needs
    if plan.tasks:
        needed = {dep for t in plan.tasks for dep in t.needs}
        for t in plan.tasks[:-1]:
            if t.id not in needed:
                issues.append(ValidationIssue(
                    "V-08", "The last task must produce the final result.", t.id))

    # V-09: constraint ids, text, state
    c_seen: set[str] = set()
    for c in plan.constraints:
        if not c.id:
            issues.append(ValidationIssue("V-09", "A constraint has no id.", constraint_id=c.id))
        if c.id in c_seen:
            issues.append(ValidationIssue("V-09", f"Constraint id {c.id} is used twice.", constraint_id=c.id))
        c_seen.add(c.id)
        if not c.text.strip():
            issues.append(ValidationIssue("V-09", f"Constraint {c.id} has no text.", constraint_id=c.id))
        elif len(c.text) > CONSTRAINT_TEXT_MAX:
            issues.append(ValidationIssue(
                "V-09", f"Constraint {c.id} is longer than {CONSTRAINT_TEXT_MAX} characters.", constraint_id=c.id))
        if c.state not in ("active", "resolved"):
            issues.append(ValidationIssue(
                "V-09", f"Constraint {c.id} has state {c.state!r}; expected active or resolved.",
                constraint_id=c.id))

    # constraint ids referenced by tasks must exist
    valid_cids = {c.id for c in plan.constraints}
    for t in plan.tasks:
        for tc in t.constraints:
            if tc not in valid_cids:
                issues.append(ValidationIssue(
                    "V-10", f"{t.id} names constraint {tc}, which does not exist.", t.id))

    # V-10: constraint scopes name existing tasks
    for c in plan.constraints:
        if isinstance(c.scope, list):
            for tid in c.scope:
                if tid not in index:
                    issues.append(ValidationIssue(
                        "V-10", f"Constraint {c.id} applies to {tid}, which does not exist.",
                        constraint_id=c.id))

    # task types and roles must be known
    for t in plan.tasks:
        if t.type not in TASK_TYPES:
            issues.append(ValidationIssue(
                "V-01", f"{t.id} has unknown type {t.type!r}.", t.id))
        if t.role not in ROLES:
            issues.append(ValidationIssue(
                "V-01", f"{t.id} has unknown role {t.role!r}.", t.id))

    return issues
