"""A deterministic, scripted mock adapter (tests, demo, self-test).

The mock speaks the adapter contract exactly.  Tests script one *turn* per
``chat()`` call; a turn is a dict with any of:

- ``text``: final text (may contain a ``<handoff>`` block)
- ``json``: an object dumped as the final text (planner replies)
- ``reasoning``: reasoning text (stored, never passed on)
- ``tool_calls``: ``[{"name": ..., "arguments": {...}}]``
- ``usage``: ``[tokens_in, tokens_out]`` (default: estimated from lengths)
- ``error``: a normalized error code to raise (``AUTH``, ``SERVER_ERROR``...)
- ``crash``: raise :class:`SimulatedCrash` mid-stream (fault injection)
- ``repeat``: yield the same tool call ``n`` times (loop detection)
- ``delay``: seconds to sleep before the first event (stall testing)
- ``overshoot``: report this many extra output tokens (cap testing)
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from las.modules.providers.base import (
    Adapter, CapabilityRecord, ChatRequest, Message, ProviderError, SimulatedCrash,
    StreamEvent, ToolCall, Usage,
)

__all__ = [
    "Adapter", "CapabilityRecord", "ChatRequest", "Message", "ProviderError",
    "SimulatedCrash", "StreamEvent", "ToolCall", "Usage",
    "MockAdapter", "default_caps",
]


def default_caps(context_length: int = 8192) -> CapabilityRecord:
    return CapabilityRecord(
        provider_id="mock", model="mock-model", reachable=True, authenticated=True,
        models_listed=True, streaming=True, tool_calling=True, json_schema=True,
        context_length=context_length, token_counting=True, reasoning=False,
        router_mode=False, server_version="mock-1.0",
        probed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


class MockAdapter(Adapter):
    """Pops one scripted turn per chat call; asserts-friendly.

    With an empty script and ``LAS_MOCK_SCRIPT`` set to a JSON file of
    turns, the script is loaded from that file (used by CLI smoke tests and
    offline demos).
    """

    provider_id = "mock"

    def __init__(self, script: Optional[list[dict]] = None,
                 caps: Optional[CapabilityRecord] = None):
        if not script and os.environ.get("LAS_MOCK_SCRIPT"):
            with open(os.environ["LAS_MOCK_SCRIPT"], encoding="utf-8") as fh:
                script = json.load(fh)
        self.script = list(script or [])
        self.cursor = 0
        self.caps = caps or default_caps()
        self.requests: list[ChatRequest] = []
        self.chat_calls = 0

    # -- scripting --------------------------------------------------------

    def add_turn(self, turn: dict) -> "MockAdapter":
        self.script.append(turn)
        return self

    def _next_turn(self) -> dict:
        if self.cursor < len(self.script):
            turn = self.script[self.cursor]
            self.cursor += 1
            return turn
        return {"text": ""}

    # -- adapter contract ---------------------------------------------------

    def probe(self, model: str) -> CapabilityRecord:
        caps = default_caps()
        caps.model = model
        return caps

    def list_models(self) -> list[dict]:
        return [{"id": self.caps.model or "mock-model", "loaded": True,
                 "context_length": self.caps.context_length, "raw": {}}]

    def count_tokens(self, messages: list[Message], model: str) -> Optional[int]:
        chars = sum(len(m.content) for m in messages)
        return int(chars / 3.5)

    def chat(self, request: ChatRequest):
        self.requests.append(request)
        self.chat_calls += 1
        turn = self._next_turn()

        if turn.get("error"):
            raise ProviderError(turn["error"], f"mock error {turn['error']}")
        if turn.get("delay"):
            time.sleep(float(turn["delay"]))

        usage_in = int(sum(len(m.content) for m in request.messages) / 3.5)
        usage_out = 0

        if turn.get("reasoning"):
            yield StreamEvent("reasoning_delta", text=turn["reasoning"])
            usage_out += max(1, len(turn["reasoning"]) // 4)

        tool_calls = turn.get("tool_calls") or []
        repeat = int(turn.get("repeat", 1))
        for i in range(repeat):
            for spec in tool_calls:
                args = spec.get("arguments", {})
                if not isinstance(args, str):
                    args = json.dumps(args, sort_keys=True)
                yield StreamEvent("tool_call", tool_call=ToolCall(
                    f"call_{self.chat_calls}_{i}", str(spec.get("name", "")), args))
                usage_out += 12

        text = turn.get("text")
        if text is None and "json" in turn:
            text = json.dumps(turn["json"], ensure_ascii=False, indent=2)
        if text:
            # Split into deltas so consumers exercise delta handling.
            step = max(1, len(text) // 8)
            for i in range(0, len(text), step):
                yield StreamEvent("text_delta", text=text[i:i + step])
            usage_out += max(1, int(len(text) / 3.5))

        if turn.get("crash"):
            raise SimulatedCrash("simulated kill during generation")

        usage_out += int(turn.get("overshoot", 0))
        if "usage" in turn:
            uin, uout = turn["usage"]
            yield StreamEvent("usage", usage=Usage(int(uin), int(uout)))
        else:
            yield StreamEvent("usage", usage=Usage(usage_in, usage_out))
        yield StreamEvent("done")
