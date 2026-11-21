"""Plan quality: validation (V rules) and linting (L rules).

Facade for the planquality module (ADR-0002). Types live in the shared
kernel (``las.shared.plan_contract``); the rules are implemented in
``.validation`` and ``.linting``.
"""

from .linting import (
    CONTEXT_WARNING, DEFAULT_HANDOFF_WORDS, LONG_INSTRUCTIONS, Finding, add_suggestions,
    errors, lint,
)
from .validation import validate_plan

__all__ = [
    "CONTEXT_WARNING", "DEFAULT_HANDOFF_WORDS", "LONG_INSTRUCTIONS", "Finding",
    "add_suggestions", "errors", "lint", "validate_plan",
]
