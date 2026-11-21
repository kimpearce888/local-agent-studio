"""Engine loopback HTTP API (spec 7.5, API-2) — M1.

Listens on 127.0.0.1 only. Every request carries ``X-Studio-Token``, a
random per-launch token stored beside the data root with mode 0600
(spec 7.5); requests with an unexpected Origin or a non-loopback Host are
refused with 403. Errors return ``{"error": {"code", "message", "fix"}}``.

Runs execute on a background thread (one at a time, RUN-2); the events
endpoint supports polling with ``?after=seq``.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from las.modules.execution import DriverConfig, Engine
from las.modules.planning import Planner
from las.modules.providers import build_adapter, probe as probe_adapter
from las.modules.storage import Store
EXIT_OK = 0
from las.shared.messages import message_with_fix
from las.shared.plan_contract import Plan
from las.shared.provider_contract import ProviderProfile

API_VERSION = "1"


class ApiError(Exception):
    def __init__(self, status: int, code: str, msg: str, fix: str = ""):
        super().__init__(msg)
        self.status, self.code, self.msg, self.fix = status, code, msg, fix


class _RunCoordinator:
    """One active run at a time (RUN-2); controls address the live driver."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active: dict[str, object] = {}   # run_id -> RunDriver

    def start(self, engine: Engine, run_id: str, registry, models, config):
        def work():
            driver = engine.driver(run_id, registry, models, config)
            with self.lock:
                self.active[run_id] = driver
            try:
                driver.run()
            finally:
                with self.lock:
                    self.active.pop(run_id, None)
                self._completion_hook(engine, run_id)

        thread = threading.Thread(target=work, daemon=True, name=f"run-{run_id}")
        thread.start()
        return thread

    def driver_for(self, run_id: str):
        with self.lock:
            driver = self.active.get(run_id)
        if driver is None:
            raise ApiError(409, "NOT-RUNNING",
                           f"Run {run_id} is not running in this engine; "
                           f"controls need an active run.")
        return driver

    @staticmethod
    def _hook_for(engine) -> str:
        try:
            return str(engine.store.get_setting("runs.completion_hook", "") or "")
        except Exception:  # noqa: BLE001 - settings must never break a run
            return ""

    def _completion_hook(self, engine, run_id):
        """API-2: a local hook on run completion. The command configured in
        settings (runs.completion_hook) runs with LAS_RUN_ID and
        LAS_RUN_STATUS in its environment. Failures are recorded, never
        raised: a broken hook must not take the engine down."""
        cmd = self._hook_for(engine)
        if not cmd:
            return
        import subprocess
        import time as _time
        try:
            status = engine.state(run_id).status
            started = _time.monotonic()
            proc = subprocess.run(
                cmd, shell=True, timeout=60, capture_output=True,
                env={**os.environ, "LAS_RUN_ID": run_id,
                     "LAS_RUN_STATUS": status})
            engine.store.append(run_id, "run.hook.executed", {
                "command": cmd, "exit_code": proc.returncode,
                "status": status,
                "duration_ms": int((_time.monotonic() - started) * 1000)})
        except Exception as exc:  # noqa: BLE001 - hook errors are data
            try:
                engine.store.append(run_id, "run.hook.executed", {
                    "command": cmd, "exit_code": -1, "error": str(exc)})
            except Exception:  # noqa: BLE001
                pass


def openapi_document(port_note: str = "http://127.0.0.1:PORT") -> dict:
    """OpenAPI 3.1 description of the engine API (API-2, spec 7.5)."""
    def op(summary, **kw):
        base = {"summary": summary, "responses": {"200": {"description": "OK"},
                "401": {"description": "Missing or wrong X-Studio-Token"},
                "409": {"description": "Refused (see error.code)"}}}
        base.update(kw)
        return base
    return {
        "openapi": "3.1.0",
        "info": {"title": "Local Agent Studio engine API",
                 "version": "1.0.0",
                 "description": "Loopback-only engine API. Every request "
                                "except this document requires the "
                                "per-launch token in X-Studio-Token."},
        "servers": [{"url": port_note}],
        "security": [{"studioToken": []}],
        "paths": {
            "/runs": {
                "get": op("List runs"),
                "post": op("Create and start a run",
                           requestBody={"content": {"application/json": {
                               "schema": {"type": "object",
                                          "properties": {
                                              "plan": {"description": "plan object or stored plan id"},
                                              "cap_tokens": {"type": "integer"},
                                              "cap_wall_minutes": {"type": "integer"}}}}}}),
            },
            "/runs/{id}": {
                "get": op("Run state", parameters=[
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "string"}}]),
                "delete": op("Delete a run and its workspace", parameters=[
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "string"}}]),
                "post": op("Control: pause|resume|cancel|approve|reject|"
                           "retry|skip|rerun", parameters=[
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "string"}}]),
            },
            "/runs/{id}/events": {
                "get": op("Events after a sequence number", parameters=[
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                    {"name": "after", "in": "query", "schema": {"type": "integer"}}]),
            },
            "/runs/{id}/tasks/{taskId}": {
                "patch": op("Edit a pending task (RUN-6)", parameters=[
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "taskId", "in": "path", "required": True, "schema": {"type": "string"}}]),
            },
            "/runs/{id}/export": {
                "get": op("Export markdown or JSON", parameters=[
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "format", "in": "query", "schema": {"type": "string",
                                       "enum": ["markdown", "json"]}}]),
            },
            "/providers": {"get": op("List providers"), "post": op("Add a provider")},
            "/providers/{id}": {
                "put": op("Update a provider"),
                "delete": op("Delete a provider"),
            },
            "/providers/{id}/probe": {"post": op("Capability probe (PRV-4)")},
            "/providers/{id}/models/{model}/load": {
                "post": op("Load a model (PRV-8, llama.cpp router)")},
            "/providers/{id}/models/{model}/unload": {
                "post": op("Unload a model (PRV-8)")},
            "/plans": {"get": op("List plans"), "post": op("Save a plan")},
            "/templates": {"get": op("List templates"), "post": op("Save a template")},
            "/openapi.json": {"get": {"summary": "This document",
                                      "security": [],
                                      "responses": {"200": {"description": "OK"}}}},
        },
        "components": {"securitySchemes": {
            "studioToken": {"type": "apiKey", "in": "header",
                            "name": "X-Studio-Token"}}},
    }


def _error_body(exc: ApiError) -> bytes:
    return json.dumps(
        {"error": {"code": exc.code, "message": exc.msg, "fix": exc.fix}},
        ensure_ascii=False).encode("utf-8")


def make_handler(data_dir: str, token: str):
    store = Store(data_dir)
    engine = Engine(data_dir)
    coordinator = _RunCoordinator()

    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalAgentStudio/" + API_VERSION

        def log_message(self, fmt, *args):  # quiet by default
            pass

        # -- security (spec 7.5) ------------------------------------------
        def _check_security(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost", "::1"):
                self._send(403, {"error": {"code": "FORBIDDEN",
                                           "message": "The engine listens on loopback only.",
                                           "fix": ""}})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and not origin.startswith(
                    ("http://127.0.0.1", "http://localhost")):
                self._send(403, {"error": {"code": "FORBIDDEN",
                                           "message": "Unexpected Origin header.",
                                           "fix": ""}})
                return False
            if self.headers.get("X-Studio-Token") != token:
                self._send(401, {"error": {"code": "AUTH",
                                           "message": "Missing or invalid engine token.",
                                           "fix": "Pass the per-launch token in X-Studio-Token."}})
                return False
            return True

        def _send(self, status: int, obj) -> None:
            body = obj if isinstance(obj, bytes) else json.dumps(
                obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(400, "BAD-REQUEST", "The request body is not valid JSON.")

        # -- routing ------------------------------------------------------
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method: str):
            try:
                if self.path == "/openapi.json" and method == "GET":
                    self._send(200, openapi_document())
                    return
                if not self._check_security():
                    return
                u = urlparse(self.path)
                self._dispatch(method, u.path, u.query)
            except ApiError as exc:
                self._send(exc.status, _error_body(exc))
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": {"code": "INTERNAL",
                                           "message": f"{type(exc).__name__}: {exc}",
                                           "fix": ""}})

        def _dispatch(self, method: str, path: str, query: str = "", *_):
            q = {k: v[0] for k, v in parse_qs(query).items()}
            parts = [p for p in path.split("/") if p]

            if method == "GET" and path == "/health":
                return self._send(200, {"status": "ok", "version": API_VERSION,
                                        "app": "local-agent-studio"})
            if parts and parts[0] == "providers":
                return self._providers(method, parts)
            if parts and parts[0] == "plans":
                return self._plans(method, parts)
            if parts and parts[0] == "runs":
                return self._runs(method, parts, q)
            if parts and parts[0] == "templates":
                if method == "GET":
                    return self._send(200, {"templates": store.list_templates()})
                if method == "POST":
                    store.save_template(self._body())
                    return self._send(200, {"saved": True})
            raise ApiError(404, "NOT-FOUND", f"No route for {method} {path}.")

        # -- resource handlers ---------------------------------------------
        def _providers(self, method, parts):
            if len(parts) == 1:
                if method == "GET":
                    return self._send(200, {"providers": [p.to_obj() for p in store.list_providers()]})
                if method == "POST":
                    body = self._body()
                    profile = ProviderProfile(
                        id=str(body.get("id") or body.get("name", "")),
                        name=str(body.get("name", body.get("id", ""))),
                        kind=str(body.get("kind", "generic")),
                        base_url=str(body.get("base_url", "")),
                        auth_env=str(body.get("auth_env", "")),
                        wire=str(body.get("wire", "chat")))
                    if not profile.id:
                        raise ApiError(422, "BAD-REQUEST", "A provider needs an id.")
                    store.save_provider(profile)
                    return self._send(200, profile.to_obj())
            pid = parts[1]
            profile = store.get_provider(pid)
            if profile is None:
                raise ApiError(404, "NOT-FOUND", f"No provider {pid}.")
            if method == "PUT":
                body = self._body()
                for key in ("name", "kind", "base_url", "auth_env", "wire"):
                    if key in body:
                        setattr(profile, key, str(body[key]))
                store.save_provider(profile)
                return self._send(200, profile.to_obj())
            if method == "DELETE":
                store.delete_provider(pid)
                return self._send(200, {"deleted": pid})
            if len(parts) == 3 and parts[2] == "probe" and method == "POST":
                adapter = build_adapter(profile)
                model = self._body().get("model") or (
                    adapter.list_models()[0]["id"] if adapter.list_models() else "")
                caps = probe_adapter(adapter, model, pid)
                return self._send(200, caps.to_obj())
            if len(parts) == 5 and parts[2] == "models" and parts[4] in ("load", "unload") and method == "POST":
                adapter = build_adapter(profile)
                model = parts[3]
                try:
                    if parts[4] == "load":
                        adapter.preload(model)
                    else:
                        adapter.unload(model)
                except Exception as exc:  # noqa: BLE001 - ProviderError
                    raise ApiError(502, "LIFECYCLE", str(exc)) from exc
                return self._send(200, {"ok": True, "model": model,
                                        "action": parts[4]})
            if len(parts) == 3 and parts[2] == "models" and method == "GET":
                try:
                    return self._send(200, {"models": build_adapter(profile).list_models()})
                except Exception as exc:  # noqa: BLE001
                    msg, fix = message_with_fix("UNREACHABLE", url=profile.base_url)
                    raise ApiError(502, "UNREACHABLE", msg, fix) from exc
            raise ApiError(404, "NOT-FOUND", f"No route for {method} /providers/{'/'.join(parts[1:])}.")

        def _plans(self, method, parts):
            if len(parts) == 1 and method == "POST":
                body = self._body()
                goal = str(body.get("goal", ""))
                if not goal:
                    raise ApiError(422, "BAD-REQUEST", "Plan generation needs a goal.")
                profile = store.list_providers()[0] if store.list_providers() else None
                if profile is None:
                    raise ApiError(409, "NO-PROVIDER", "No provider is configured.")
                adapter = build_adapter(profile)
                model = str(body.get("model", "")) or (
                    adapter.list_models()[0]["id"] if adapter.list_models() else "")
                result = Planner(adapter, model).plan(goal, {
                    "granularity": body.get("granularity", "normal"),
                    "max_tasks": int(body.get("max_tasks", 30))})
                if result.plan is not None:
                    store.save_plan(result.plan.to_obj())
                return self._send(200, {
                    "valid": result.valid, "partial": result.partial,
                    "issues": [str(i) for i in result.issues],
                    "plan": result.plan.to_obj() if result.plan else None})
            plan_id = parts[1]
            obj = store.get_plan(plan_id)
            if obj is None:
                raise ApiError(404, "NOT-FOUND", f"No plan {plan_id}.")
            if method == "GET":
                return self._send(200, obj)
            if method == "PUT":
                new_obj = self._body()
                Plan.from_obj(new_obj)      # 422 on a bad shape
                store.save_plan(new_obj)
                return self._send(200, new_obj)
            raise ApiError(404, "NOT-FOUND", f"No route for {method} /plans/{plan_id}.")

        def _runs(self, method, parts, q):
            if len(parts) == 2 and method == "DELETE":
                if parts[1] not in store.run_ids():
                    raise ApiError(404, "NOT-FOUND", f"No run {parts[1]}.")
                store.delete_run(parts[1])
                return self._send(200, {"deleted": parts[1]})
            if len(parts) == 1 and method == "GET":
                rows = []
                for run_id in store.run_ids():
                    try:
                        state = engine.state(run_id)
                    except (ValueError, Exception):
                        continue
                    rows.append(state.to_obj())
                return self._send(200, {"runs": rows})
            if len(parts) == 1 and method == "POST":
                body = self._body()
                plan_obj = body.get("plan")
                if isinstance(plan_obj, dict):
                    plan = Plan.from_obj(plan_obj)
                else:
                    plan_obj = store.get_plan(str(plan_obj or ""))
                    if plan_obj is None:
                        raise ApiError(422, "BAD-REQUEST",
                                       "Provide a plan object or a stored plan id.")
                    plan = Plan.from_obj(plan_obj)
                profiles = store.list_providers()
                if not profiles:
                    raise ApiError(409, "NO-PROVIDER", "No provider is configured.")
                from las.modules.providers import ProviderRegistry
                registry = ProviderRegistry()
                for p in profiles:
                    registry.register(p.id, build_adapter(p))
                run_id = engine.create_run(
                    plan, cap_tokens=body.get("cap_tokens"),
                    cap_wall_minutes=body.get("cap_wall_minutes"))
                models = {"default": (profiles[0].id, str(body.get("model", "")))}
                coordinator.start(engine, run_id, registry, models,
                                  DriverConfig(failure_policy=body.get("on_failure", "ask")))
                return self._send(200, {"run_id": run_id})
            run_id = parts[1]
            try:
                state = engine.state(run_id)
            except ValueError as exc:
                raise ApiError(404, "NOT-FOUND", str(exc))
            if len(parts) == 2 and method == "GET":
                return self._send(200, state.to_obj())
            if len(parts) == 3 and parts[2] == "events" and method == "GET":
                after = int(q.get("after", 0))
                events = [e.to_obj() for e in store.events(run_id, after=after)]
                return self._send(200, {"events": events})
            if len(parts) == 3 and parts[2] == "export" and method == "GET":
                fmt = q.get("format", "markdown")
                if fmt not in ("markdown", "json"):
                    raise ApiError(422, "BAD-REQUEST", "format must be markdown or json.")
                try:
                    from las.modules.reporting import export_run  # optional-dep: reporting
                except ImportError:
                    raise ApiError(409, "MODULE-UNAVAILABLE",
                                   "The reporting module is not available.",
                                   "studio modules list shows installed modules.")
                result = export_run(state, store, fmt=fmt)
                return self._send(200, {"content": result.content,
                                        "size_bytes": result.size_bytes})
            if len(parts) == 3 and parts[2] in ("pause", "resume", "cancel") and method == "POST":
                driver = coordinator.driver_for(run_id)
                if parts[2] == "pause":
                    driver.request_pause_now()
                elif parts[2] == "cancel":
                    driver.request_cancel()
                else:
                    driver.step()
                return self._send(200, {"ok": True, "control": parts[2]})
            if len(parts) == 5 and parts[2] == "tasks" and method == "POST":
                driver = coordinator.driver_for(run_id)
                task_id, action = parts[3], parts[4]
                if action == "retry":
                    driver.retry(task_id)
                elif action == "skip":
                    driver.skip(task_id)
                elif action == "rerun":
                    driver.rerun_from(task_id)
                else:
                    raise ApiError(404, "NOT-FOUND", f"Unknown task control {action}.")
                return self._send(200, {"ok": True, "task": task_id, "action": action})
            if len(parts) == 5 and parts[2] == "gates" and method == "POST":
                driver = coordinator.driver_for(run_id)
                task_id, action = parts[3], parts[4]
                if action == "approve":
                    driver.approve_gate(task_id, reason=self._body().get("reason", ""))
                elif action == "reject":
                    driver.reject_gate(task_id, decision=self._body().get("decision", "stop"))
                else:
                    raise ApiError(404, "NOT-FOUND", f"Unknown gate action {action}.")
                return self._send(200, {"ok": True})
            if len(parts) == 4 and parts[2] == "handoffs" and method == "PUT":
                body = self._body()
                handoff_obj = body.get("handoff")
                if not isinstance(handoff_obj, dict):
                    raise ApiError(422, "BAD-REQUEST", "Provide a handoff object.")
                task_id = parts[3]
                store.write_handoff(run_id, task_id, handoff_obj)
                store.append(run_id, "handoff.edited", {
                    "task_id": task_id, "handoff": handoff_obj,
                    "revision": body.get("revision", 1),
                    "reason": str(body.get("reason", ""))})
                return self._send(200, {"ok": True})
            raise ApiError(404, "NOT-FOUND", f"No route for {method} {self.path}.")

    return Handler


def serve(data_dir: str, host: str = "127.0.0.1", port: int = 0):
    """Start the engine API; returns (server, token, token_path).

    The token is written to ``<data_dir>/api-token`` with mode 0600
    (spec 7.5). Disablement of the api module is enforced by the host
    command, not here.
    """
    token = secrets.token_hex(24)
    token_path = os.path.join(data_dir, "api-token")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
              "w", encoding="utf-8") as fh:
        fh.write(token)
    handler = make_handler(data_dir, token)
    server = ThreadingHTTPServer((host, port), handler)
    return server, token, token_path


def run_server(data_dir: str, port: int = 0) -> int:
    """Serve until interrupted; prints the bound port and token path."""
    server, _token, token_path = serve(data_dir, port=port)
    print(f"Engine API on http://127.0.0.1:{server.server_address[1]} "
          f"(token file: {token_path}, mode 0600)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


__all__ = ["API_VERSION", "ApiError", "make_handler", "run_server", "serve"]
