"""Provider presets — moved to ``las.modules.providers.presets`` (ADR-0004).

Compatibility re-export at the pre-migration path; step S7 removes it.
"""

from las.modules.providers import PRESETS, Preset

__all__ = ["PRESETS", "Preset"]
