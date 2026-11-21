"""Planner — moved to ``las.modules.planning`` (ADR-0004).

Compatibility re-export at the pre-migration path; removed in migration
step S7 (ADR-0004).
"""

from las.modules.planning import (  # noqa: F401
    MAX_REPAIR_ROUNDS, PLAN_JSON_SCHEMA, PLANNER_SYSTEM_PROMPT, SCHEMA_AS_TEXT,
    PlanResult, Planner, extract_constraint_candidates, find_source_span,
)

__all__ = [
    "PLANNER_SYSTEM_PROMPT", "MAX_REPAIR_ROUNDS", "PLAN_JSON_SCHEMA", "SCHEMA_AS_TEXT",
    "extract_constraint_candidates", "find_source_span", "PlanResult", "Planner",
]
