"""las/linter.py — moved to ``las.modules.planquality`` (ADR-0004).

Compatibility re-export at the pre-migration path; removed in migration step S7 (ADR-0004).
"""

from las.modules.planquality import Finding, lint, errors, CONTEXT_WARNING  # noqa: F401

__all__ = ["Finding", "lint", "errors", "CONTEXT_WARNING", ]
