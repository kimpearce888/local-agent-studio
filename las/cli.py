"""CLI — moved to ``las.app.cli`` (ADR-0004).

Compatibility re-export at the pre-migration path; removed in migration
step S7 (ADR-0004).
"""

from las.app.cli import (  # noqa: F401
    EXIT_CANCELLED, EXIT_CAP, EXIT_GATE, EXIT_INTERNAL, EXIT_INVALID, EXIT_OK,
    EXIT_PROVIDER, EXIT_RUN_FAILED, build_parser, default_data_dir, main,
)

__all__ = [
    "EXIT_OK", "EXIT_RUN_FAILED", "EXIT_INVALID", "EXIT_PROVIDER", "EXIT_GATE",
    "EXIT_CAP", "EXIT_CANCELLED", "EXIT_INTERNAL", "build_parser",
    "default_data_dir", "main",
]
