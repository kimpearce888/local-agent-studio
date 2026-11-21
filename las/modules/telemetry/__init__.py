"""Optional OTLP export (OBS-5), off by default.

Maps run and task executions to OTLP spans with GenAI semantic-convention
attributes (``gen_ai.*``). The convention version is pinned here and
recorded on every export; it changes only with a deliberate update.
Endpoints default to local-only (loopback); anything else requires
``telemetry.otlp_local_only = false``. Failures are printed to stderr and
never fail a run.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from typing import Optional

from las.modules.storage import Store

PINNED_CONVENTION = "gen_ai semconv 1.0.0 (attribute prefix gen_ai.*)"
DEFAULT_LOCAL_ONLY = True


def is_local(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _attrs(pairs: list[tuple[str, object]]) -> list[dict]:
    out = []
    for key, value in pairs:
        kind = "stringValue"
        if isinstance(value, bool):
            kind = "boolValue"
        elif isinstance(value, int):
            kind = "intValue"
        out.append({"key": key, "value": {kind: value}})
    return out


def _id_hex(text: str, length: int) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _unix_nano(iso: str) -> int:
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(iso.rstrip("Z"))
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_spans(store: Store, run_id: str) -> list[dict]:
    """One run span plus one span per task, from the event log."""
    from las.modules.execution import Engine
    state = Engine(store.data_dir).state(run_id)
    events = store.events(run_id)
    by_task: dict[str, list] = {}
    for ev in events:
        tid = (ev.payload or {}).get("task_id")
        if tid:
            by_task.setdefault(tid, []).append(ev)
    trace = _id_hex(run_id, 32)

    def ts(task_events, *types):
        for ev in task_events:
            if ev.type in types:
                return ev.ts
        return events[0].ts if events else ""

    run_start = events[0].ts if events else ""
    run_end = events[-1].ts if events else ""
    spans = [{
        "traceId": trace, "spanId": _id_hex(run_id + ":run", 16),
        "name": "gen_ai.run", "kind": 1,
        "startTimeUnixNano": _unix_nano(run_start),
        "endTimeUnixNano": _unix_nano(run_end),
        "attributes": _attrs([
            ("gen_ai.operation.name", "run"),
            ("gen_ai.system", "local-agent-studio"),
            ("las.run.id", run_id),
            ("las.run.status", state.status),
            ("las.run.tokens", state.tokens_used()),
            ("las.telemetry.convention", PINNED_CONVENTION),
        ]),
    }]
    for task in state.plan.tasks:
        tevs = by_task.get(task.id, [])
        usage = state.tasks.get(task.id).usage if state.tasks.get(task.id) else {}
        attempts = state.tasks.get(task.id).attempts if state.tasks.get(task.id) else []
        model = attempts[-1].model if attempts else ""
        spans.append({
            "traceId": trace, "spanId": _id_hex(run_id + ":" + task.id, 16),
            "name": f"gen_ai.task {task.id}", "kind": 1,
            "startTimeUnixNano": _unix_nano(ts(tevs, "task.ready",
                                               "attempt.started", "gate.waiting")),
            "endTimeUnixNano": _unix_nano(ts(tevs, "task.succeeded",
                                             "task.failed", "task.skipped")),
            "attributes": _attrs([
                ("gen_ai.operation.name", "execute_task"),
                ("gen_ai.system", attempts[-1].provider if attempts else ""),
                ("gen_ai.request.model", model),
                ("gen_ai.usage.input_tokens",
                 usage.get("input_tokens", 0) if usage else 0),
                ("gen_ai.usage.output_tokens",
                 usage.get("output_tokens", 0) if usage else 0),
                ("las.run.id", run_id),
                ("las.task.id", task.id),
                ("las.task.status",
                 state.tasks[task.id].state if state.tasks.get(task.id) else "pending"),
                ("las.telemetry.convention", PINNED_CONVENTION),
            ]),
        })
    return spans


def maybe_export(store: Store, run_id: str,
                 stderr: Optional[object] = None) -> Optional[dict]:
    """Export a finished run if telemetry is configured (OBS-5).

    Off unless ``telemetry.otlp_endpoint`` is set. Local endpoints only,
    unless ``telemetry.otlp_local_only`` is explicitly false.
    """
    endpoint = str(store.get_setting("telemetry.otlp_endpoint", "") or "")
    if not endpoint:
        return None                      # off by default
    local_only = store.get_setting("telemetry.otlp_local_only",
                                   DEFAULT_LOCAL_ONLY)
    if local_only and not is_local(endpoint):
        print(f"telemetry: endpoint {endpoint} is not local; "
              f"set telemetry.otlp_local_only=false to allow it.",
              file=stderr or sys.stderr)
        return None
    url = endpoint.rstrip("/") + "/v1/traces"
    body = {
        "resourceSpans": [{
            "resource": {"attributes": _attrs([
                ("service.name", "local-agent-studio"),
                ("las.telemetry.convention", PINNED_CONVENTION)])},
            "scopeSpans": [{"scope": {"name": "las-telemetry", "version": "1.0"},
                            "spans": build_spans(store, run_id)}],
        }],
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"status": resp.status, "spans": len(body["resourceSpans"][0]
                                                       ["scopeSpans"][0]["spans"])}
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a run
        print(f"telemetry: export to {url} failed: {exc}",
              file=stderr or sys.stderr)
        return None


__all__ = ["PINNED_CONVENTION", "build_spans", "is_local", "maybe_export"]
