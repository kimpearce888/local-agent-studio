"""Provider adapter contract (spec 7.1) and the internal message model.

One internal message model, shaped like Responses items (decision D2); two
wire adapters (Chat Completions and Responses) sit behind it.  The engine is
stateless toward servers: every request carries the full history (D3).

This is the shared kernel port: providers (and the mock) implement it, the
engine consumes it, and nothing on either side imports the other's
internals (ADR-0001).  ``ProviderProfile`` (the persisted connection
profile) and ``SimulatedCrash`` (the fault-injection signal used by the
crash-resume tests, AC-06) live here so no production module ever imports
the mock provider (finding F-02).
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


class SimulatedCrash(Exception):
    """Propagates out of the driver, leaving a truncated event log (AC-06).

    Raised by fault-injection adapters (the mock); the driver re-raises it
    so a fresh engine instance must replay, mark the orphaned attempt
    aborted and continue.  Defined here so the engine never depends on the
    mock provider module.
    """


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


@dataclass
class ProviderProfile:
    """A persisted provider connection profile (spec 6.4, PRV-1).

    Owned by the provider contract, persisted opaquely by the storage
    module; keys are only ever ``env:NAME`` references, never stored values.
    """

    id: str
    name: str
    kind: str                    # preset name or "mock" or "generic"
    base_url: str = ""
    auth_env: str = ""           # env:NAME reference; keys are never stored in plans/events
    wire: str = "chat"           # chat (Chat Completions) | responses (Open Responses)
    extra_headers: dict = field(default_factory=dict)
    timeout_seconds: float = 60.0
    created_at: str = ""

    def to_obj(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "base_url": self.base_url,
            "auth_env": self.auth_env, "wire": self.wire, "extra_headers": self.extra_headers,
            "timeout_seconds": self.timeout_seconds, "created_at": self.created_at,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> "ProviderProfile":
        return cls(**{k: obj.get(k, cls.__dataclass_fields__[k].default)
                      for k in ("id", "name", "kind", "base_url", "auth_env", "wire",
                                "extra_headers", "timeout_seconds", "created_at")})
