"""Plan schema v1 types and parsing (spec 6.1) — shared kernel contract.

Types, constants, strict parsing, variable substitution and the cycle
helper live here because six or more modules consume them. The V-02..V-12
validation rules are business logic and live in the planquality module
(``las.schema.plan`` re-exports them during the migration; see ADR-0004).
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
    # Public alias below; kept private for internal use.

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


# Public constructor used by execution (replan) and planning.
task_from_obj = _task_from_obj
