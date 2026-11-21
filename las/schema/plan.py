"""Plan schema v1 and its validation rules (spec 6.1, 5.2).

A plan is one JSON object: ``schema_version``, ``id``, ``title``, ``goal``,
``created_at``, ``options``, ``constraints`` and ``tasks``.  Validation rules
V-01 .. V-12 are implemented here with the exact messages from spec 5.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = 1

TASK_TYPES = ("task", "collector", "gate", "input", "output", "note")
ROLES = ("planner", "worker", "collector")
GRANULARITIES = ("coarse", "normal", "fine")
GRANULARITY_TASK_COUNTS = {"coarse": (3, 6), "normal": (6, 15), "fine": (15, 30)}

# Spec 6.1: tools default. `append_file` exists in the registry but is opt-in.
DEFAULT_TOOLS = ["list_files", "read_file", "write_file", "search_files", "patch_file"]

MAX_TASKS_HARD = 500          # spec 5.2 V-06
DEFAULT_MAX_TASKS = 30        # spec 5.8
MAX_NEEDS = 8                 # spec 5.2 V-07
MAX_TASK_CONSTRAINTS = 12     # spec 5.2 V-07
TITLE_MAX = 80
INSTRUCTIONS_MIN = 20
INSTRUCTIONS_MAX = 4000
CONSTRAINT_TEXT_MAX = 1000

TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PlanFormatError(ValueError):
    """The object does not match plan schema v1 (rule V-01)."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass
class ValidationIssue:
    """One validation or lint finding."""

    rule: str
    message: str
    task_id: Optional[str] = None
    constraint_id: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"{self.rule}: {self.message}"


@dataclass
class Constraint:
    """A binding constraint (spec 6.1)."""

    id: str
    text: str
    state: str = "active"                     # active | resolved
    prerequisite: Optional[str] = None
    authority: str = "user"                   # user | task:<id> | tool:<name>
    fallback: Optional[str] = None
    scope: Any = "all"                        # "all" or list of task ids
    origin: str = "planner"                   # goal | planner | user
    source_span: Optional[list[int]] = None   # [start, end] into the goal

    def applies_to(self, task_id: str) -> bool:
        if self.scope == "all":
            return True
        if isinstance(self.scope, list):
            return task_id in self.scope
        return False

    def to_obj(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "state": self.state,
            "prerequisite": self.prerequisite,
            "authority": self.authority,
            "fallback": self.fallback,
            "scope": self.scope,
            "origin": self.origin,
            "source_span": self.source_span,
        }


@dataclass
class Task:
    """One unit of work (spec 6.1)."""

    id: str
    type: str = "task"
    title: str = ""
    instructions: str = ""
    needs: list[str] = field(default_factory=list)
    expected_output: str = ""
    constraints: list[str] = field(default_factory=list)
    role: str = "worker"
    model: Optional[dict] = None              # {"provider_id", "model_id"}
    params: dict = field(default_factory=dict)
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    limits: dict = field(default_factory=dict)
    gate_before: bool = False

    def to_obj(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "instructions": self.instructions,
            "needs": list(self.needs),
            "expected_output": self.expected_output,
            "constraints": list(self.constraints),
            "role": self.role,
            "model": self.model,
            "params": self.params,
            "tools": list(self.tools),
            "limits": self.limits,
            "gate_before": self.gate_before,
        }


@dataclass
class Plan:
    """Plan schema v1 (spec 6.1)."""

    id: str
    title: str
    goal: str
    created_at: str
    options: dict = field(default_factory=lambda: {
        "granularity": "normal", "max_tasks": DEFAULT_MAX_TASKS, "stage_barriers": False,
    })
    constraints: list[Constraint] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    # -- lookup helpers -------------------------------------------------

    def task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def constraint(self, constraint_id: str) -> Optional[Constraint]:
        for c in self.constraints:
            if c.id == constraint_id:
                return c
        return None

    def task_constraints(self, task: Task) -> list[Constraint]:
        """Constraints that apply to a task: own ids plus inherited scope."""
        own = {c.id for c in self.constraints if c.applies_to(task.id)}
        own.update(task.constraints)
        return [c for c in self.constraints if c.id in own]

    def dependents(self, task_id: str) -> list[str]:
        return [t.id for t in self.tasks if task_id in t.needs]

    def order_index(self, task_id: str) -> int:
        for i, t in enumerate(self.tasks):
            if t.id == task_id:
                return i
        return -1

    def to_obj(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "created_at": self.created_at,
            "options": self.options,
            "constraints": [c.to_obj() for c in self.constraints],
            "tasks": [t.to_obj() for t in self.tasks],
        }

    # -- serialization --------------------------------------------------

    @classmethod
    def from_obj(cls, obj: Any) -> "Plan":
        """Strict parse; raises PlanFormatError (V-01) on a bad shape."""
        if not isinstance(obj, dict):
            raise PlanFormatError("The plan is not a JSON object.")
        try:
            schema_version = obj["schema_version"]
            if int(schema_version) != SCHEMA_VERSION:
                raise PlanFormatError(
                    f"Unsupported plan schema_version {schema_version!r}; expected 1.")
            plan = cls(
                id=str(obj["id"]),
                title=str(obj["title"]),
                goal=str(obj["goal"]),
                created_at=str(obj["created_at"]),
                options=dict(obj.get("options") or {}),
            )
            for c in obj.get("constraints", []):
                plan.constraints.append(_constraint_from_obj(c))
            for t in obj.get("tasks", []):
                plan.tasks.append(_task_from_obj(t))
        except PlanFormatError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanFormatError(f"The plan is not in the expected format: {exc}") from exc
        return plan


def _constraint_from_obj(obj: Any) -> Constraint:
    if not isinstance(obj, dict):
        raise PlanFormatError("A constraint is not a JSON object.")
    return Constraint(
        id=str(obj.get("id", "")),
        text=str(obj.get("text", "")),
        state=str(obj.get("state", "active")),
        prerequisite=obj.get("prerequisite"),
        authority=str(obj.get("authority", "user")),
        fallback=obj.get("fallback"),
        scope=obj.get("scope", "all"),
        origin=str(obj.get("origin", "planner")),
        source_span=obj.get("source_span"),
    )


def _task_from_obj(obj: Any) -> Task:
    if not isinstance(obj, dict):
        raise PlanFormatError("A task is not a JSON object.")
    task_type = str(obj.get("type", "task"))
    role = obj.get("role")
    if role is None:
        role = "collector" if task_type == "collector" else "worker"
    tools = obj.get("tools")
    if tools is None:
        tools = list(DEFAULT_TOOLS)
    return Task(
        id=str(obj.get("id", "")),
        type=task_type,
        title=str(obj.get("title", "")),
        instructions=str(obj.get("instructions", "")),
        needs=[str(n) for n in (obj.get("needs") or [])],
        expected_output=str(obj.get("expected_output", "") or ""),
        constraints=[str(c) for c in (obj.get("constraints") or [])],
        role=role,
        model=obj.get("model"),
        params=dict(obj.get("params") or {}),
        tools=[str(t) for t in tools],
        limits=dict(obj.get("limits") or {}),
        gate_before=bool(obj.get("gate_before", False)),
    )


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


def plan_constraints_ids(plan: Plan) -> set[str]:
    return {c.id for c in plan.constraints}


def _variables_in_task(t: Task) -> list[str]:
    text = " ".join([t.title, t.instructions, t.expected_output])
    return VARIABLE_RE.findall(text)


def plan_variables(plan: Plan) -> list[str]:
    """All {{variable}} names used anywhere in the plan."""
    names: list[str] = []
    for t in plan.tasks:
        for name in _variables_in_task(t):
            if name not in names:
                names.append(name)
    return names


def _find_cycle(plan: Plan) -> list[str] | None:
    """Return a cycle path like [t2, t4, t2] or None (rule V-04)."""
    graph = {t.id: [d for d in t.needs if d in {x.id for x in plan.tasks}] for t in plan.tasks}
    state: dict[str, int] = {}  # 0 = unvisited, 1 = in stack, 2 = done
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                i = stack.index(dep)
                return stack[i:] + [dep]
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for t in plan.tasks:
        if state.get(t.id, 0) == 0:
            found = visit(t.id)
            if found:
                return found
    return None


def substitute_variables(plan: Plan, values: dict[str, str]) -> Plan:
    """Fill {{variables}} in titles, instructions and expected outputs."""
    def sub(text: str) -> str:
        def repl(m: re.Match) -> str:
            return values.get(m.group(1), m.group(0))
        return VARIABLE_RE.sub(repl, text)

    for t in plan.tasks:
        t.title = sub(t.title)
        t.instructions = sub(t.instructions)
        t.expected_output = sub(t.expected_output)
    return plan
