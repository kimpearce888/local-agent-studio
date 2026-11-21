"""Provider adapter contract (spec 7.1) and the internal message model.

One internal message model, shaped like Responses items (decision D2); two
wire adapters (Chat Completions and Responses) sit behind it.  The engine is
stateless toward servers: every request carries the full history (D3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator, Optional

# Normalized adapter error codes (spec 7.1)
ERROR_CODES = (
    "AUTH", "UNREACHABLE", "TIMEOUT", "MODEL_NOT_FOUND", "CONTEXT_EXCEEDED",
    "RATE_LIMIT", "SERVER_ERROR", "BAD_REQUEST",
)


class ProviderError(Exception):
    """A normalized provider failure."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = "{}"        # JSON text; parsed by the broker, not the model


@dataclass
class Message:
    """One item of the internal message model."""

    role: str                    # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""       # for role == "tool": the call this answers
    name: str = ""               # tool name for role == "tool"
    untrusted: bool = False      # content from files/web/tool output (TLS-4)

    def to_obj(self) -> dict:
        obj: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            obj["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in self.tool_calls]
        if self.tool_call_id:
            obj["tool_call_id"] = self.tool_call_id
        if self.name:
            obj["name"] = self.name
        return obj


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)


@dataclass
class ChatRequest:
    model: str
    messages: list[Message]
    tools: list[ToolDef] = field(default_factory=list)
    json_schema: Optional[dict] = None     # constrained output schema (PLN-3)
    params: dict = field(default_factory=dict)   # temperature, top_p, max_tokens, seed, stop
    stream: bool = True


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class StreamEvent:
    """Events a chat stream yields (spec 7.1)."""

    kind: str                    # text_delta | reasoning_delta | tool_call | usage | done | error
    text: str = ""
    tool_call: Optional[ToolCall] = None
    usage: Optional[Usage] = None
    error: Optional[ProviderError] = None


@dataclass
class CapabilityRecord:
    """Results of the capability probe (spec 7.3), cached 24 h."""

    provider_id: str
    model: str
    reachable: bool = False
    authenticated: bool = False
    models_listed: bool = False
    streaming: bool = False
    tool_calling: bool = False
    json_schema: bool = False
    context_length: Optional[int] = None
    token_counting: bool = False
    reasoning: bool = False
    router_mode: bool = False
    server_version: str = ""
    probed_at: str = ""

    def to_obj(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_obj(cls, obj: dict) -> "CapabilityRecord":
        return cls(**{k: obj.get(k) for k in cls.__dataclass_fields__ if k in obj})


class Adapter:
    """Base class for provider adapters."""

    def probe(self, model: str) -> CapabilityRecord:
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        raise NotImplementedError

    def chat(self, request: ChatRequest) -> Iterator[StreamEvent]:
        raise NotImplementedError

    def cancel(self, stream_id: str) -> None:  # pragma: no cover - default no-op
        return None

    def count_tokens(self, messages: list[Message], model: str) -> Optional[int]:
        return None

    def preload(self, model: str) -> None:  # P1 (PRV-8)
        raise ProviderError("BAD_REQUEST", "Model lifecycle is not supported by this server.")

    def unload(self, model: str) -> None:  # P1 (PRV-8)
        raise ProviderError("BAD_REQUEST", "Model lifecycle is not supported by this server.")


def safe_json_loads(text: str) -> Optional[object]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
