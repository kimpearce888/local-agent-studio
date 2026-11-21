"""Handoff schema — moved to ``las.modules.handoffs`` (ADR-0004).

Compatibility re-export; removed in migration step S7.
"""

from las.modules.handoffs import (
    HandoffConstraint, Decision, Deliverable, EvidenceContext, Handoff,
    KeyFact, OpenIssue, constraint_hash, extract_handoff, normalize_ws,
    strip_code_fences, validate_handoff,
)

__all__ = [
    "HandoffConstraint", "Decision", "Deliverable", "EvidenceContext", "Handoff",
    "KeyFact", "OpenIssue", "constraint_hash", "extract_handoff", "normalize_ws",
    "strip_code_fences", "validate_handoff",
]
