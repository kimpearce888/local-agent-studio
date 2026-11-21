"""Provider adapter contract (spec 7.1) — moved to ``las.shared.provider_contract``.

Compatibility re-export at the pre-migration path; step S7 removes it.
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
