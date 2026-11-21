"""Self-test — moved to ``las.modules.selftest`` (ADR-0004).

Compatibility re-export at the pre-migration path; step S7 removes it.
"""

from las.modules.selftest import run_selftest

__all__ = ["run_selftest"]
