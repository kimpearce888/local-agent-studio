"""Diagnostics bundle (OBS-7, spec S13): a redacted local report.

Everything the bundle contains is listed before saving; keys are removed,
prompt and goal text are replaced by lengths and SHA-256 hashes, and
nothing is ever sent anywhere. The host passes module and capability
summaries in, so this module imports neither the registry nor providers.
"""

from __future__ import annotations

import hashlib
import platform
import sys
from typing import Optional

from las.modules.execution import Engine
from las.modules.storage import Store


def text_meta(text: str) -> dict:
    """Redaction unit: length + hash instead of content (spec S13)."""
    return {"length": len(text), "sha256": hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:16]}


def build_diagnostics(store: Store, engine: Engine, modules: list[dict],
                      capabilities: Optional[list[dict]] = None) -> dict:
    """Assemble the redacted diagnostics bundle.

    ``modules``: [{id, version, core, enabled, summary}] from the host.
    ``capabilities``: provider capability records (probe results).
    """
    capabilities = capabilities or []
    runs = []
    for run_id in store.run_ids():
        try:
            state = engine.state(run_id)
        except Exception:  # noqa: BLE001 - diagnostics never crash
            runs.append({"run_id": run_id, "error": "state-unreadable"})
            continue
        tasks = {tid: {"state": ts.state, "attempts": len(ts.attempts)}
                 for tid, ts in state.tasks.items()}
        runs.append({
            "run_id": run_id,
            "status": state.status,
            "title": text_meta(state.title or ""),
            "goal": text_meta(state.plan.goal),
            "tasks": tasks,
            "tokens_used": state.tokens_used(),
            "events": len(state.events),
            "chain_verified": store.verify(run_id),
        })
    return {
        "schema": "las-diagnostics/1",
        "app": {"name": "local-agent-studio", "python": sys.version.split()[0],
                "platform": platform.platform()},
        "modules": modules,
        "providers": [
            {"id": p.id, "kind": p.kind, "base_url": p.base_url,
             "auth_env_reference": bool(p.auth_env)}
            for p in store.list_providers()],
        "capabilities": capabilities,
        "settings": _redacted_settings(store),
        "runs": runs,
    }


def _redacted_settings(store: Store) -> dict:
    """Public settings only; nothing secret is stored, but be explicit."""
    keys = ("runs.failure_policy", "runs.cap_tokens", "runs.cap_wall_minutes",
            "data.retention", "export.ai_notice", "export.redact_prompts",
            "updates.channel", "updates.auto", "privacy.crash_reports")
    return {k: store.get_setting(k) for k in keys}


def diagnostics_items() -> list[str]:
    """What the bundle contains, shown to the user before saving (S13)."""
    return [
        "App, Python and platform versions",
        "Module registry state (ids, versions, enabled flags)",
        "Providers: id, kind, base URL, whether a key reference exists (never the key)",
        "Saved capability probe results",
        "Non-sensitive settings",
        "Per run: status, task states, token totals, event count, chain-verification",
        "Goal and titles as length + SHA-256 only (never the text)",
    ]


__all__ = ["build_diagnostics", "diagnostics_items", "text_meta"]
