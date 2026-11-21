"""The network log (spec S11 Privacy): every connection the app made.

All outbound traffic in the desktop app flows through the provider
registry (model lists, capability probes, planner jobs, run sessions),
so a logging proxy around each adapter sees every request.  Entries
are kept in memory and mirrored into the store; the log is read-only
for the user.
"""

from __future__ import annotations

import time

MAX_ENTRIES = 200


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LoggingAdapter:
    """Wrap an adapter and record every request it serves."""

    def __init__(self, inner, sink: list, store=None):
        self._inner = inner
        self._sink = sink
        self._store = store

    def _log(self, action: str, ok: bool, detail: str = "") -> None:
        url = getattr(getattr(self._inner, "core", None), "base_url",
                      "") or ""
        entry = {"ts": _now(), "action": action, "url": url,
                 "ok": ok, "detail": detail[:120]}
        self._sink.append(entry)
        del self._sink[:-MAX_ENTRIES]
        if self._store is not None:
            try:
                self._store.set_setting("network_log", list(self._sink))
            except Exception:                          # noqa: BLE001
                pass

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def list_models(self):
        try:
            result = self._inner.list_models()
            self._log("model-list", True, f"{len(result)} models")
            return result
        except Exception as exc:                      # noqa: BLE001
            self._log("model-list", False, str(exc))
            raise

    def chat(self, request):
        try:
            result = self._inner.chat(request)
            self._log("chat", True, request.model)
            return result
        except Exception as exc:                      # noqa: BLE001
            self._log("chat", False, str(exc))
            raise


def recent(store) -> list[dict]:
    """The persisted network log (newest last)."""
    try:
        return store.get_setting("network_log") or []
    except Exception:                                  # noqa: BLE001
        return []
