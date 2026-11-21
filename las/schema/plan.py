"""Plan schema and validation — moved (ADR-0004).

Types live in ``las.shared.plan_contract``; validation rules in
``las.modules.planquality``. Compatibility re-export; removed in S7.
"""

from las.modules.planquality import validate_plan
from las.shared.plan_contract import (  # noqa: F401
    CONSTRAINT_TEXT_MAX, DEFAULT_MAX_TASKS, DEFAULT_TOOLS, GRANULARITIES,
    GRANULARITY_TASK_COUNTS, INSTRUCTIONS_MAX, INSTRUCTIONS_MIN, MAX_NEEDS,
    MAX_TASKS_HARD, MAX_TASK_CONSTRAINTS, ROLES, SCHEMA_VERSION, TASK_ID_RE,
    TASK_TYPES, TITLE_MAX, VARIABLE_RE, Constraint, Plan, PlanFormatError, Task,
    ValidationIssue, plan_constraints_ids, plan_variables, substitute_variables,
)

__all__ = [
    "SCHEMA_VERSION", "TASK_TYPES", "ROLES", "GRANULARITIES", "GRANULARITY_TASK_COUNTS",
    "DEFAULT_TOOLS", "MAX_TASKS_HARD", "DEFAULT_MAX_TASKS", "MAX_NEEDS",
    "MAX_TASK_CONSTRAINTS", "TITLE_MAX", "INSTRUCTIONS_MIN", "INSTRUCTIONS_MAX",
    "CONSTRAINT_TEXT_MAX", "TASK_ID_RE", "VARIABLE_RE",
    "PlanFormatError", "ValidationIssue", "Constraint", "Task", "Plan",
    "validate_plan", "plan_constraints_ids", "plan_variables", "substitute_variables",
]
