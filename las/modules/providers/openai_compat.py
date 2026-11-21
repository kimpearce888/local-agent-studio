"""Wire adapters for OpenAI-compatible servers (spec 7.1, 7.2).

Two adapters share one HTTP core: Chat Completions and Responses (Open
Responses compatible, decision D2).  The engine always sends full history
and never a previous response id (D3).  Provider-side tools are never
enabled; the Unsloth preset adds ``enable_tools: false`` to every request
(PRV-5, AC-09).

Only the standard library is used: urllib for HTTP, hand-rolled SSE parsing.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Iterator, Optional

from las.modules.providers.base import (
    Adapter, CapabilityRecord, ChatRequest, Message, ProviderError, StreamEvent,
    ToolCall, Usage, safe_json_loads,
)
from las.modules.providers.presets import PRESETS, Preset


def resolve_api_key(auth_env: str) -> Optional[str]:
    """Resolve an ``env:NAME`` key reference.  Keys never enter the store."""
    if not auth_env:
        return None
    if auth_env.startswith("env:"):
        return os.environ.get(auth_env[4:])
    return auth_env


class HttpCore:
    """Shared HTTP plumbing with normalized errors (spec 7.1)."""

    def __init__(self, base_url: str, api_key: Optional[str], timeout: float = 60.0,
                 extra_headers: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self._open_streams: dict[str, object] = {}

    def headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        h.update(self.extra_headers)
        return h

    def url(self, path: str) -> str:
        # Tolerate base URLs that already include /v1.
        if path.startswith("/v1/") and self.base_url.endswith("/v1"):
            path = path[3:]
        return self.base_url + path

    def request_json(self, method: str, path: str, body: dict | None = None,
                     timeout: float | None = None) -> tuple[int, object]:
        """One request returning (status, parsed JSON or text)."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url(path), data=data,
                                     headers=self.headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, _parse_body(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise _http_error(exc.code, raw) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError("TIMEOUT", f"The server did not answer in time: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                raise ProviderError("TIMEOUT", f"The server did not answer in time: {exc}") from exc
            raise ProviderError("UNREACHABLE", f"Cannot reach the server: {exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise ProviderError("UNREACHABLE", f"Cannot reach the server: {exc}") from exc

    def stream_sse(self, path: str, body: dict, stream_id: str,
                   timeout: float | None = None) -> Iterator[tuple[str, object]]:
        """POST and yield SSE ``data:`` payloads as parsed JSON, plus [DONE]."""
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url(path), data=data,
                                     headers=self.headers(), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            raise _http_error(exc.code, raw) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError("TIMEOUT", f"The server did not answer in time: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                raise ProviderError("TIMEOUT", f"The server did not answer in time: {exc}") from exc
            raise ProviderError("UNREACHABLE", f"Cannot reach the server: {exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise ProviderError("UNREACHABLE", f"Cannot reach the server: {exc}") from exc

        self._open_streams[stream_id] = resp
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    yield "[DONE]", None
                    return
                parsed = safe_json_loads(payload)
                if parsed is not None:
                    yield "json", parsed
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError("TIMEOUT", f"The stream stalled: {exc}") from exc
        except (ConnectionError, OSError) as exc:
            raise ProviderError("UNREACHABLE", f"The connection dropped: {exc}") from exc
        finally:
            self._open_streams.pop(stream_id, None)
            try:
                resp.close()
            except Exception:
                pass

    def cancel(self, stream_id: str) -> None:
        resp = self._open_streams.get(stream_id)
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def _parse_body(raw: str):
    parsed = safe_json_loads(raw)
    return parsed if parsed is not None else raw


def _http_error(status: int, body: str) -> ProviderError:
    text = ""
    if isinstance(body, dict):
        err = body.get("error") or {}
        text = str(err.get("message") or body)
    else:
        text = str(body)[:300]
    lowered = text.lower()
    if status in (401, 403):
        return ProviderError("AUTH", "The server rejected the key.", status)
    if status == 404:
        if "model" in lowered:
            return ProviderError("MODEL_NOT_FOUND", text or "Model not found.", status)
        return ProviderError("MODEL_NOT_FOUND" if "model" in lowered else "BAD_REQUEST",
                             text or "Not found.", status)
    if status == 429:
        return ProviderError("RATE_LIMIT", text or "Rate limited.", status)
    if status >= 500:
        return ProviderError("SERVER_ERROR", text or "Server error.", status)
    if status == 400 and ("context" in lowered or "too long" in lowered
                          or "maximum" in lowered and "token" in lowered):
        return ProviderError("CONTEXT_EXCEEDED", text or "Context exceeded.", status)
    return ProviderError("BAD_REQUEST", text or "Bad request.", status)


# -- Chat Completions adapter ----------------------------------------------


class ChatCompletionsAdapter(Adapter):
    """Maps the internal model to ``POST /v1/chat/completions``."""

    def __init__(self, provider_id: str, base_url: str, api_key: Optional[str] = None,
                 preset: str | Preset = "generic", timeout: float = 60.0,
                 extra_headers: Optional[dict] = None):
        self.provider_id = provider_id
        self.preset = PRESETS[preset] if isinstance(preset, str) else preset
        self.core = HttpCore(base_url, api_key, timeout, extra_headers)
        self._stream_seq = 0

    # -- requests -----------------------------------------------------------

    def _body(self, request: ChatRequest, stream: bool) -> dict:
        body: dict = {
            "model": request.model,
            "messages": [self._message_to_wire(m) for m in request.messages],
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if request.tools:
            body["tools"] = [{
                "type": "function",
                "function": {"name": t.name, "description": t.description,
                             "parameters": t.parameters or {"type": "object"}},
            } for t in request.tools]
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "plan", "schema": request.json_schema,
                                "strict": False},
            }
        for key in ("temperature", "top_p", "max_tokens", "seed", "stop", "context_length"):
            if key in request.params and request.params[key] is not None:
                body[key] = request.params[key]
        # PRV-5: never rely on provider-side tools; Unsloth always disables them.
        body.update(self.preset.extra_body)
        return body

    @staticmethod
    def _message_to_wire(m: Message) -> dict:
        obj: dict = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            obj["tool_calls"] = [{
                "id": c.id, "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            } for c in m.tool_calls]
            if not m.content:
                obj["content"] = None
        if m.role == "tool":
            obj = {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
            if m.name:
                obj["name"] = m.name
        return obj

    # -- adapter contract -----------------------------------------------------

    def preload(self, model: str) -> None:
        """Load a model (PRV-8). Only implemented for presets whose server
        documents lifecycle endpoints (llama.cpp router mode, spec 7.2);
        LM Studio's native v1 paths are an open item confirmed in Phase 0
        (spec section 12) and are deliberately not guessed."""
        paths = getattr(self.preset, "lifecycle_paths", None)
        if not paths:
            raise ProviderError(
                "BAD_REQUEST",
                "Model load is not available for this server. "
                "The native endpoints are confirmed in Phase 0 (spec 7.2).")
        status, body = self.core.request_json(
            "POST", paths[0], body={"model": model})
        if status >= 400:
            raise ProviderError(
                "SERVER_ERROR", f"The server refused to load {model} "
                                f"(HTTP {status}).")

    def unload(self, model: str) -> None:
        paths = getattr(self.preset, "lifecycle_paths", None)
        if not paths:
            raise ProviderError(
                "BAD_REQUEST",
                "Model unload is not available for this server. "
                "The native endpoints are confirmed in Phase 0 (spec 7.2).")
        status, body = self.core.request_json(
            "POST", paths[1], body={"model": model})
        if status >= 400:
            raise ProviderError(
                "SERVER_ERROR", f"The server refused to unload {model} "
                                f"(HTTP {status}).")

    def list_models(self) -> list[dict]:
        status, body = self.core.request_json("GET", "/v1/models")
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return []
        models = []
        for item in body["data"]:
            if not isinstance(item, dict):
                continue
            models.append({
                "id": str(item.get("id", "")),
                "loaded": bool(item.get("loaded", item.get("state") == "loaded")),
                "context_length": item.get("max_context_length") or item.get("context_length"),
                "raw": item,
            })
        return models

    def chat(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_seq += 1
        stream_id = f"cc-{self._stream_seq}"
        body = self._body(request, stream=True)
        acc: dict[int, dict] = {}
        usage: Usage | None = None
        for kind, payload in self.core.stream_sse("/v1/chat/completions", body, stream_id):
            if kind == "[DONE]":
                break
            chunk = payload
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("usage"), dict):
                u = chunk["usage"]
                usage = Usage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                yield StreamEvent("text_delta", text=delta["content"])
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                yield StreamEvent("reasoning_delta", text=reasoning)
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", 0))
                slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        for idx in sorted(acc):
            slot = acc[idx]
            if not slot["id"]:
                slot["id"] = f"call_{stream_id}_{idx}"
            yield StreamEvent("tool_call", tool_call=ToolCall(
                slot["id"], slot["name"], slot["arguments"]))
        if usage is not None:
            yield StreamEvent("usage", usage=usage)
        yield StreamEvent("done")

    def cancel(self, stream_id: str) -> None:
        self.core.cancel(stream_id)

    def count_tokens(self, messages: list[Message], model: str) -> Optional[int]:
        if not self.preset.count_tokens_path:
            return None
        body = {"model": model,
                "messages": [self._message_to_wire(m) for m in messages]}
        try:
            _status, parsed = self.core.request_json("POST", self.preset.count_tokens_path, body)
        except ProviderError:
            return None
        if isinstance(parsed, dict):
            for key in ("token_count", "count", "total_tokens", "tokens"):
                if isinstance(parsed.get(key), int):
                    return parsed[key]
        if isinstance(parsed, int):
            return parsed
        return None


# -- Responses adapter (Open Responses compatible) ---------------------------


class ResponsesAdapter(Adapter):
    """Maps the internal model to ``POST /v1/responses``.

    Full history always goes in ``input``; no previous response id is ever
    sent (D3).  Streaming events follow the Responses event names.
    """

    def __init__(self, provider_id: str, base_url: str, api_key: Optional[str] = None,
                 preset: str | Preset = "generic", timeout: float = 60.0,
                 extra_headers: Optional[dict] = None):
        self.provider_id = provider_id
        self.preset = PRESETS[preset] if isinstance(preset, str) else preset
        self.core = HttpCore(base_url, api_key, timeout, extra_headers)
        self._stream_seq = 0

    def _body(self, request: ChatRequest) -> dict:
        instructions_parts = [m.content for m in request.messages if m.role == "system"]
        input_items: list[dict] = []
        for m in request.messages:
            if m.role == "system":
                continue
            if m.role == "assistant" and m.tool_calls:
                for c in m.tool_calls:
                    input_items.append({"type": "function_call", "call_id": c.id,
                                        "name": c.name, "arguments": c.arguments})
                if m.content:
                    input_items.append({"role": "assistant", "content": m.content})
            elif m.role == "tool":
                input_items.append({"type": "function_call_output", "call_id": m.tool_call_id,
                                    "output": m.content})
            else:
                input_items.append({"role": m.role, "content": m.content})
        body: dict = {"model": request.model, "input": input_items, "stream": True}
        if instructions_parts:
            body["instructions"] = "\n\n".join(instructions_parts)
        if request.tools:
            body["tools"] = [{"type": "function", "name": t.name,
                              "description": t.description,
                              "parameters": t.parameters or {"type": "object"}}
                             for t in request.tools]
        if request.json_schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "plan",
                                       "schema": request.json_schema}}
        for key in ("temperature", "top_p", "max_tokens", "seed", "stop"):
            if key in request.params and request.params[key] is not None:
                body[key] = request.params[key]
        body.update(self.preset.extra_body)
        return body

    def preload(self, model: str) -> None:
        """Load a model (PRV-8). Only implemented for presets whose server
        documents lifecycle endpoints (llama.cpp router mode, spec 7.2);
        LM Studio's native v1 paths are an open item confirmed in Phase 0
        (spec section 12) and are deliberately not guessed."""
        paths = getattr(self.preset, "lifecycle_paths", None)
        if not paths:
            raise ProviderError(
                "BAD_REQUEST",
                "Model load is not available for this server. "
                "The native endpoints are confirmed in Phase 0 (spec 7.2).")
        status, body = self.core.request_json(
            "POST", paths[0], body={"model": model})
        if status >= 400:
            raise ProviderError(
                "SERVER_ERROR", f"The server refused to load {model} "
                                f"(HTTP {status}).")

    def unload(self, model: str) -> None:
        paths = getattr(self.preset, "lifecycle_paths", None)
        if not paths:
            raise ProviderError(
                "BAD_REQUEST",
                "Model unload is not available for this server. "
                "The native endpoints are confirmed in Phase 0 (spec 7.2).")
        status, body = self.core.request_json(
            "POST", paths[1], body={"model": model})
        if status >= 400:
            raise ProviderError(
                "SERVER_ERROR", f"The server refused to unload {model} "
                                f"(HTTP {status}).")

    def list_models(self) -> list[dict]:
        status, body = self.core.request_json("GET", "/v1/models")
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return []
        return [{"id": str(i.get("id", "")),
                 "loaded": bool(i.get("loaded", False)),
                 "context_length": i.get("max_context_length") or i.get("context_length"),
                 "raw": i} for i in body["data"] if isinstance(i, dict)]

    def chat(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_seq += 1
        stream_id = f"rs-{self._stream_seq}"
        body = self._body(request)
        for kind, payload in self.core.stream_sse("/v1/responses", body, stream_id):
            if kind == "[DONE]":
                break
            ev = payload
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type", "")
            if etype == "response.output_text.delta":
                yield StreamEvent("text_delta", text=str(ev.get("delta", "")))
            elif etype == "response.reasoning.delta":
                yield StreamEvent("reasoning_delta", text=str(ev.get("delta", "")))
            elif etype == "response.output_item.done":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    yield StreamEvent("tool_call", tool_call=ToolCall(
                        str(item.get("call_id") or item.get("id") or ""),
                        str(item.get("name", "")),
                        str(item.get("arguments", "{}"))))
            elif etype == "response.completed":
                resp = ev.get("response") or {}
                u = (resp.get("usage") or {})
                usage = Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)))
                yield StreamEvent("usage", usage=usage)
        yield StreamEvent("done")

    def cancel(self, stream_id: str) -> None:
        self.core.cancel(stream_id)
