"""Provider layer — moved to ``las.modules.providers`` (ADR-0004).

This package remains as a compatibility re-export during the modular
migration; step S7 removes it. Import from ``las.modules.providers`` in
new code.
"""

from las.modules.providers import *  # noqa: F401,F403 - re-export for compat
from las.modules.providers import __all__  # noqa: F401
