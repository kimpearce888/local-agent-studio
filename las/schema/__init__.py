"""Schema package — moved to the shared kernel and feature modules (ADR-0004).

Plan types: ``las.shared.plan_contract``; validation and linting:
``las.modules.planquality``; handoffs: ``las.modules.handoffs``.
Compatibility re-exports; removed in migration step S7.
"""

from las.modules.handoffs import (  # noqa: F401
    HandoffConstraint, Decision, Deliverable, EvidenceContext, Handoff,
    KeyFact, OpenIssue, constraint_hash, extract_handoff, normalize_ws,
    strip_code_fences, validate_handoff,
)
from las.modules.planquality import validate_plan  # noqa: F401
from las.shared.plan_contract import (  # noqa: F401
    Constraint, DEFAULT_TOOLS, Plan, PlanFormatError, Task, ValidationIssue,
    plan_variables, substitute_variables,
)

__all__ = [
    "Constraint", "Plan", "Task", "PlanFormatError", "ValidationIssue", "validate_plan",
    "plan_variables", "substitute_variables", "DEFAULT_TOOLS",
    "HandoffConstraint", "Decision", "Deliverable", "EvidenceContext", "Handoff",
    "KeyFact", "OpenIssue", "constraint_hash", "extract_handoff", "normalize_ws",
    "validate_handoff",
]
