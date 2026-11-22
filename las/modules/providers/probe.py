"""Capability probe (spec 7.3).

Seven steps, at most six small requests, at most 2,000 tokens.  Results are
cached by provider, model and server version for 24 hours (the cache lives
in the store's settings table; the probe itself is stateless).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Optional

from las.modules.providers.base import (
    Adapter, CapabilityRecord, ChatRequest, Message, ProviderError, ToolDef,
    safe_json_loads,
)
from las.modules.handoffs import strip_code_fences
from las.modules.providers.presets import DETECT_PORTS, PRESETS

TINY_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def probe(adapter: Adapter, model: str, provider_id: str = "") -> CapabilityRecord:
    caps = CapabilityRecord(provider_id=provider_id or getattr(adapter, "provider_id", ""),
                            model=model)
    caps.probed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1. model list: reachable, authenticated, models available
    try:
        models = adapter.list_models()
        caps.reachable = True
        caps.authenticated = True
        caps.models_listed = bool(models)
        for m in models:
            if m.get("id") == model and m.get("context_length"):
                caps.context_length = int(m["context_length"])
        raw = next((m.get("raw", {}) for m in models if m.get("id") == model), {})
        caps.server_version = str(raw.get("version") or raw.get("server_version") or "")
    except ProviderError as exc:
        if exc.code == "AUTH":
            caps.reachable = True
            caps.authenticated = False
        return caps

    # 2. a 5-token streamed chat: streaming and time to first token
    try:
        start = time.monotonic()
        got_delta = False
        text = ""
        for ev in adapter.chat(ChatRequest(
                model=model,
                messages=[Message("user", "Reply with the single word OK.")],
                params={"max_tokens": 5})):
            if ev.kind == "text_delta":
                if not got_delta:
                    got_delta = True
                    _ttft = time.monotonic() - start
                text += ev.text
        caps.streaming = got_delta
    except ProviderError:
        caps.streaming = False

    # 3. ask the model to call a trivial ping tool
    try:
        for ev in adapter.chat(ChatRequest(
                model=model,
                messages=[Message("user", "Call the ping tool now.")],
                tools=[ToolDef("ping", "Reply with pong.", {"type": "object"})],
                params={"max_tokens": 20})):
            if ev.kind == "tool_call":
                caps.tool_calling = True
    except ProviderError:
        pass

    # 4. a tiny JSON schema
    try:
        text = ""
        for ev in adapter.chat(ChatRequest(
                model=model,
                messages=[Message("user", 'Reply with JSON {"ok": true}.')],
                json_schema=TINY_SCHEMA,
                params={"max_tokens": 20})):
            if ev.kind == "text_delta":
                text += ev.text
        # The planner parses replies with strip_code_fences; the probe
        # must measure the same thing, or fenced-JSON models (GPT, GLM,
        # Claude all do it) get json_schema under-reported and the
        # planner then runs without the schema.
        parsed = safe_json_loads(strip_code_fences(text))
        caps.json_schema = isinstance(parsed, dict) and parsed.get("ok") is True
    except ProviderError:
        pass

    # 5. token counting endpoint
    try:
        n = adapter.count_tokens([Message("user", "hello")], model)
        caps.token_counting = n is not None
    except ProviderError:
        caps.token_counting = False

    # 6. context length fallback: leave Unknown when absent (manual field)

    # 7. llama.cpp router mode
    preset = getattr(adapter, "preset", None)
    if preset is not None and getattr(preset, "id", "") == "llamacpp":
        try:
            core = getattr(adapter, "core", None)
            if core is not None:
                _status, body = core.request_json("GET", "/models")
                caps.router_mode = isinstance(body, dict) and "models" in body
        except ProviderError:
            caps.router_mode = False

    return caps


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def detect_servers(ports: tuple[int, ...] = DETECT_PORTS, timeout: float = 1.0) -> list[dict]:
    """Probe well-known local ports (spec S1 step 2).

    One unauthenticated model-list request per port, 1 second timeout, no
    credentials sent, no redirects followed.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    found: list[dict] = []
    guesses = {1234: "lmstudio", 11434: "ollama", 8080: "llamacpp",
               8000: "vllm", 8888: "unsloth"}
    for port in ports:
        url = f"http://localhost:{port}/v1/models"
        req = urllib.request.Request(url, method="GET",
                                     headers={"Accept": "application/json"})
        status: Optional[int] = None
        try:
            with opener.open(req, timeout=timeout) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception:
            continue
        entry = {"port": port, "status": status,
                 "guess": guesses.get(port, "generic"),
                 "preset": PRESETS.get(guesses.get(port, "generic"), PRESETS["generic"])}
        if status == 200:
            entry["reachable"] = True
            entry["auth"] = "none"
        elif status in (401, 403):
            entry["reachable"] = True
            entry["auth"] = "needed"
        else:
            entry["reachable"] = False
        found.append(entry)
    return [f for f in found if f.get("reachable")]
