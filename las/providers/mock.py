"""Mock provider — moved to ``las.modules.providers.mock`` (ADR-0004).

Compatibility re-export at the pre-migration path; step S7 removes it.
"""

from las.modules.providers.mock import (
    Adapter, CapabilityRecord, ChatRequest, MockAdapter, Message, ProviderError,
    SimulatedCrash, StreamEvent, ToolCall, Usage, default_caps,
)

__all__ = [
    "Adapter", "CapabilityRecord", "ChatRequest", "MockAdapter", "Message",
    "ProviderError", "SimulatedCrash", "StreamEvent", "ToolCall", "Usage",
    "default_caps",
]
