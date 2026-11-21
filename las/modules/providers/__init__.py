"""Provider layer: adapters over open specifications (PRD section 6)."""

from __future__ import annotations

from .base import (
    Adapter, CapabilityRecord, ChatRequest, Message, ProviderError, SimulatedCrash,
    StreamEvent, ToolCall, ToolDef, Usage,
)
from .openai_compat import ChatCompletionsAdapter, ResponsesAdapter, resolve_api_key
from .presets import PRESETS, Preset
from .probe import detect_servers, probe
from las.shared.provider_contract import ProviderProfile


class ProviderRegistry:
    """Maps provider ids to adapter instances (built from profiles)."""

    def __init__(self):
        self._adapters: dict[str, Adapter] = {}

    def register(self, provider_id: str, adapter: Adapter) -> None:
        self._adapters[provider_id] = adapter

    def get(self, provider_id: str) -> Adapter:
        try:
            return self._adapters[provider_id]
        except KeyError:
            raise ProviderError(
                "UNREACHABLE", f"No provider named {provider_id!r} is configured.") from None

    def __contains__(self, provider_id: str) -> bool:
        return provider_id in self._adapters


def build_adapter(profile: ProviderProfile) -> Adapter:
    """Build a wire adapter from a stored profile (spec 7.1, 7.2).

    Mock profiles are served by the ``providers.mock`` child module,
    imported on demand so that removing that module (production builds)
    leaves this package importable; a mock profile then fails with the
    actionable ``mock module not installed`` error.
    """
    if profile.kind == "mock":
        try:
            from .mock import MockAdapter
        except ImportError as exc:
            raise ProviderError(
                "UNREACHABLE",
                "The mock provider module is not installed "
                "(studio modules list shows installed modules).") from exc
        return MockAdapter()
    preset = PRESETS.get(profile.kind, PRESETS["generic"])
    base_url = profile.base_url or preset.default_base_url
    api_key = resolve_api_key(profile.auth_env) if profile.auth_env else None
    wire = getattr(profile, "wire", "chat") or "chat"
    cls = ResponsesAdapter if wire == "responses" else ChatCompletionsAdapter
    return cls(profile.id, base_url, api_key=api_key, preset=preset,
               timeout=profile.timeout_seconds or 60.0,
               extra_headers=profile.extra_headers or {})


def registry_from_profiles(profiles: list[ProviderProfile],
                           overrides: dict[str, Adapter] | None = None) -> ProviderRegistry:
    """Build a registry; ``overrides`` replaces built adapters (tests, mock)."""
    reg = ProviderRegistry()
    for p in profiles:
        if overrides and p.id in overrides:
            reg.register(p.id, overrides[p.id])
        else:
            reg.register(p.id, build_adapter(p))
    return reg


__all__ = [
    "Adapter", "CapabilityRecord", "ChatRequest", "Message", "ProviderError",
    "SimulatedCrash", "StreamEvent", "ToolCall", "ToolDef", "Usage",
    "ChatCompletionsAdapter", "ResponsesAdapter", "resolve_api_key",
    "PRESETS", "Preset", "probe", "detect_servers",
    "ProviderProfile", "ProviderRegistry", "build_adapter", "registry_from_profiles",
]
