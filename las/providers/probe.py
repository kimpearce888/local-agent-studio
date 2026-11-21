"""Capability probe — moved to ``las.modules.providers.probe`` (ADR-0004).

Compatibility re-export at the pre-migration path; step S7 removes it.
"""

from las.modules.providers import detect_servers, probe

__all__ = ["detect_servers", "probe"]
