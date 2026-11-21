"""Provider adapter contract (spec 7.1) — moved to ``las.shared.provider_contract``.

This module remains as a compatibility re-export during the modular
migration (ADR-0004, step S7 removes it). Import from ``las.shared`` in
new code.
"""

from las.shared.provider_contract import (
    ERROR_CODES, Adapter, CapabilityRecord, ChatRequest, Message, ProviderError,
    ProviderProfile, SimulatedCrash, StreamEvent, ToolCall, ToolDef, Usage,
    safe_json_loads,
)

__all__ = [
    "ERROR_CODES", "Adapter", "CapabilityRecord", "ChatRequest", "Message",
    "ProviderError", "ProviderProfile", "SimulatedCrash", "StreamEvent",
    "ToolCall", "ToolDef", "Usage", "safe_json_loads",
]
