"""Provider presets (spec 7.2).

Presets carry the per-server defaults and quirks.  The engine never relies
on provider-side tools (PRV-5): the Unsloth preset sends ``enable_tools:
false`` on *every* request (AC-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Preset:
    id: str
    label: str
    default_base_url: str
    auth: str = "none"                 # none | bearer | optional
    notes: str = ""
    extra_body: dict = field(default_factory=dict)
    supports_lifecycle: bool = False   # preload / unload (PRV-8, P1)
    lifecycle_paths: tuple[str, str] | None = None   # (load, unload)
    count_tokens_path: str | None = None
    detect_ports: tuple[int, ...] = ()


LM_STUDIO = Preset(
    id="lmstudio", label="LM Studio", default_base_url="http://localhost:1234",
    auth="optional",
    notes="OpenAI-style endpoints for inference; native v1 API for model load and "
          "unload (P1); JSON schema through grammar sampling.",
    supports_lifecycle=True,
    detect_ports=(1234,),
)

UNSLOTH = Preset(
    id="unsloth", label="Unsloth Studio (Beta)", default_base_url="http://localhost:8888",
    auth="bearer",
    notes="Bearer key starting sk-unsloth; models list has a loaded flag; token "
          "counting at /v1/chat/count_tokens; server-side tools are always disabled.",
    extra_body={"enable_tools": False},
    count_tokens_path="/v1/chat/count_tokens",
    detect_ports=(8000, 8888),
)

OLLAMA = Preset(
    id="ollama", label="Ollama", default_base_url="http://localhost:11434",
    auth="none",
    notes="OpenAI-compatible endpoints under /v1.",
    detect_ports=(11434,),
)

LLAMA_CPP = Preset(
    id="llamacpp", label="llama.cpp server", default_base_url="http://localhost:8080",
    auth="optional",
    notes="Router mode adds /models, /models/load and /models/unload.",
    supports_lifecycle=True,
    lifecycle_paths=("/models/load", "/models/unload"),
    detect_ports=(8080,),
)

VLLM = Preset(
    id="vllm", label="vLLM", default_base_url="http://localhost:8000",
    auth="optional",
    notes="Structured-output support depends on the server version.",
    detect_ports=(8000,),
)

GENERIC = Preset(
    id="generic", label="Generic OpenAI-compatible", default_base_url="",
    auth="optional",
    notes="The capability probe decides what is used.",
)

PRESETS: dict[str, Preset] = {
    p.id: p for p in (LM_STUDIO, UNSLOTH, OLLAMA, LLAMA_CPP, VLLM, GENERIC)
}

# Ports the setup wizard probes (spec S1 step 2): one unauthenticated
# model-list request, 1 second timeout, no credentials, no redirects.
DETECT_PORTS = (1234, 11434, 8080, 8000, 8888)
