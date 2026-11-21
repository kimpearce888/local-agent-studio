"""The ``studio`` command line (spec 7.4).

Exit codes: 0 run succeeded · 1 run failed · 2 invalid plan or arguments ·
3 provider unreachable or authentication failed · 4 waiting at a gate in
non-interactive mode · 5 a cap was reached · 6 run cancelled · 7 internal
error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from las import __version__
from las.app.capabilities import require_module, set_enabled
from las.app.registry import ModuleDisabledError, ModuleNotInstalledError, ModuleRegistry
from las.modules.planquality import errors as lint_errors, lint
from las.shared.messages import message, message_with_fix
from las.modules.planning import Planner
from las.modules.providers import (
    PRESETS, ProviderRegistry, build_adapter, probe as probe_adapter,
    detect_servers,
)
from las.shared.provider_contract import ProviderError
from las.modules.execution import DriverConfig, Engine
from las.modules.planquality import validate_plan
from las.shared.plan_contract import (
    Plan, PlanFormatError, plan_variables, substitute_variables,
)
from las.modules.storage import ProviderProfile, Store, TamperedLogError
from las.modules.security import KNOWN_TOOLS

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_INVALID = 2
EXIT_PROVIDER = 3
EXIT_GATE = 4
EXIT_CAP = 5
EXIT_CANCELLED = 6
EXIT_INTERNAL = 7


def default_data_dir() -> str:
    env = os.environ.get("LAS_DATA_DIR")
    if env:
        return env
    home = os.path.expanduser("~")
        # Platform per-user application data folder (spec 6.4).
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    if sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    return os.path.join(base, "LocalAgentStudio")


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _load_plan_arg(store: Store, name: str, variables: dict) -> Plan:
    """Load a plan from a JSON file path or a stored plan/template id."""
    if os.path.exists(name):
        with open(name, encoding="utf-8") as fh:
            obj = json.load(fh)
    else:
        obj = store.get_plan(name)
        if obj is None:
            templates = store.list_templates()
            obj = next((t.get("plan") for t in templates if t.get("id") == name), None)
        if obj is None:
            print(f"plan not found: {name} (no file, no stored plan or template)")
            raise SystemExit(EXIT_INVALID)
    plan = Plan.from_obj(obj)
    if variables:
        substitute_variables(plan, variables)
    return plan


def _registry_for(store: Store, provider_name: Optional[str]) -> tuple[ProviderRegistry, str]:
    profiles = store.list_providers()
    # Authoritative enablement boundary: a mock profile needs the
    # providers.mock module (which is independently disableable).
    if any(p.kind == "mock" for p in profiles):
        require_module(ModuleRegistry(), "providers.mock", store.get_setting)
    if provider_name:
        profile = next((p for p in profiles if p.id == provider_name or p.name == provider_name),
                       None)
        if profile is None:
            raise SystemExit(f"no provider named {provider_name!r}; run: studio providers add")
        registry = ProviderRegistry()
        registry.register(profile.id, build_adapter(profile))
        return registry, profile.id
    registry = ProviderRegistry()
    for p in profiles:
        registry.register(p.id, build_adapter(p))
    default = profiles[0].id if profiles else ""
    return registry, default


# -- subcommands -------------------------------------------------------------


def cmd_plan(args) -> int:
    store = Store(args.data_dir)
    registry, provider_id = _registry_for(store, args.provider)
    if not provider_id:
        print(message("UNREACHABLE", url="(none)"))
        print("Fix: add a provider first:  studio providers add --name my-server "
              "--preset lmstudio")
        return EXIT_PROVIDER
    adapter = registry.get(provider_id)
    model = args.model
    if not model:
        try:
            models = adapter.list_models()
        except ProviderError as exc:
            msg, fix = message_with_fix(exc.code, url=getattr(adapter, "core", None) and
                                        adapter.core.base_url or "")
            print(f"{exc.code}: {msg}\nFix: {fix}")
            return EXIT_PROVIDER
        if not models:
            print(f"MODEL_NOT_FOUND: no models are listed by {provider_id}")
            return EXIT_PROVIDER
        model = models[0]["id"]
    planner = Planner(adapter, model)
    result = planner.plan(args.goal, {
        "granularity": args.granularity, "max_tasks": args.max_tasks})
    if result.error:
        msg, fix = message_with_fix(result.error)
        print(f"{result.error}: {msg}\nFix: {fix}")
        return EXIT_PROVIDER
    payload = {
        "valid": result.valid,
        "partial": result.partial,
        "repair_rounds": result.repair_rounds,
        "issues": [str(i) for i in result.issues],
        "plan": result.plan.to_obj() if result.plan else None,
    }
    if args.json_out:
        _print_json(payload)
    else:
        status = "valid" if result.valid else (
            "partial (opened with errors)" if result.partial else "failed")
        print(f"Plan: {status}  (repair rounds: {result.repair_rounds})")
        for i in result.issues:
            print(f"  {i.rule}: {i.message}")
        if result.plan:
            print(f"  {len(result.plan.tasks)} tasks, {len(result.plan.constraints)} constraints")
    if result.plan is not None:
        store.save_plan(result.plan.to_obj())
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(result.plan.to_obj(), fh, ensure_ascii=False, indent=2)
            if not args.json_out:
                print(f"Saved to {args.output}")
    return EXIT_OK if result.valid else EXIT_INVALID


def cmd_run(args) -> int:
    store = Store(args.data_dir)
    variables = dict(v for v in (kv.split("=", 1) for kv in args.var) if len(v) == 2)
    plan = _load_plan_arg(store, args.plan, variables)
    if args.goal:
        plan.goal = args.goal

    missing_vars = [v for v in plan_variables(plan) if v not in variables]
    issues = validate_plan(plan, known_tools=KNOWN_TOOLS)
    if issues or missing_vars:
        n = len(issues) + len(missing_vars)
        print(message("PLAN-INVALID", n=n))
        for i in issues:
            print(f"  {i.rule}: {i.message}")
        for v in missing_vars:
            print(f"  V-12: The variable {v} has no value. (--var {v}=...)")
        return EXIT_INVALID

    registry, provider_id = _registry_for(store, args.provider)
    if not provider_id:
        print("No provider configured. Add one first:  studio providers add --name my-server "
              "--preset lmstudio")
        return EXIT_PROVIDER

    # Provider reachability (L-09 preflight).
    reachable: dict[str, bool] = {}
    referenced = {t.model["provider_id"] for t in plan.tasks if t.model} | {provider_id}
    for pid in referenced:
        try:
            registry.get(pid).list_models()
            reachable[pid] = True
        except (ProviderError, KeyError) as exc:
            reachable[pid] = False
            code = exc.code if isinstance(exc, ProviderError) else "UNREACHABLE"
            msg, fix = message_with_fix(code)
            print(f"{code}: {msg}\nFix: {fix}")
            return EXIT_PROVIDER

    findings = lint(plan, context_length=args.context_length,
                    provider_reachable=reachable, default_provider_id=provider_id,
                    tool_calling=True)
    errs = lint_errors(findings)
    if errs:
        print(message("PLAN-INVALID", n=len(errs)))
        for f in findings:
            print(f"  {f.rule} [{f.severity}]: {f.message}")
        return EXIT_INVALID
    warnings = [f for f in findings if f.severity == "warning"]
    if warnings and not args.force:
        print("Warnings (run anyway with --force):")
        for f in warnings:
            print(f"  {f.rule}: {f.message}")
        return EXIT_INVALID

    engine = Engine(args.data_dir)
    run_id = engine.create_run(plan, cap_tokens=args.cap_tokens,
                               cap_wall_minutes=args.cap_wall_minutes)
    print(f"Run {run_id} started ({len(plan.tasks)} tasks)")
    return _drive(engine, store, run_id, registry, provider_id, args)


def _drive(engine: Engine, store: Store, run_id: str, registry: ProviderRegistry,
           provider_id: str, args) -> int:
    worker_model = args.worker_model or ""
    if not worker_model:
        try:
            models = registry.get(provider_id).list_models()
            worker_model = models[0]["id"] if models else ""
        except (ProviderError, KeyError, IndexError):
            worker_model = ""
    models = {"default": (provider_id, worker_model)}
    if getattr(args, "planner_model", None):
        models["planner"] = (provider_id, args.planner_model)
    config = DriverConfig(
        failure_policy=args.on_failure,
        cap_tokens_run=args.cap_tokens,
        cap_wall_minutes_run=args.cap_wall_minutes,
        context_length=getattr(args, "context_length", 8192),
        enable_shell=getattr(args, "enable_shell", False),
        enable_web=getattr(args, "enable_web", False),
        command_allowlist=(args.command_allowlist or "").split(",")
        if getattr(args, "command_allowlist", None) else [],
        web_domains=(args.web_domains or "").split(",")
        if getattr(args, "web_domains", None) else [])
    try:
        driver = engine.driver(run_id, registry, models, config)
    except Exception as exc:
        print(f"INTERNAL: {exc}")
        return EXIT_INTERNAL
    try:
        results = driver.run(pause_after=args.pause_after)
    except KeyboardInterrupt:
        driver.request_cancel()
        try:
            driver.step()
        except Exception:
            pass
        print("Run cancelled.")
        return EXIT_CANCELLED

    exit_code = EXIT_OK
    for r in results:
        if args.json_out:
            continue
        if r.kind == "task_succeeded":
            print(f"  task {r.task_id}: succeeded ({r.usage.get('tokens_in', 0)}+"
                  f"{r.usage.get('tokens_out', 0)} tokens)")
        elif r.kind == "task_skipped":
            print(f"  task {r.task_id}: skipped ({r.code})")
        elif r.kind == "task_failed":
            print(f"  task {r.task_id}: FAILED {r.code}")
        elif r.kind == "gate_waiting":
            print(f"  task {r.task_id}: waiting at a gate ({r.detail})")
        elif r.kind == "run_succeeded":
            print(f"Run succeeded ({r.usage.get('tasks_succeeded', 0)}/"
                  f"{r.usage.get('tasks_total', 0)} tasks, {r.usage.get('tokens', 0)} tokens)")
        elif r.kind == "run_failed":
            print(f"Run failed: {r.code or 'unknown'}")
        elif r.kind == "paused":
            print(f"Run paused: {r.detail}")

    last = results[-1] if results else None
    if last is not None:
        if last.kind == "run_succeeded":
            exit_code = EXIT_OK
        elif last.kind == "run_failed":
            exit_code = EXIT_CAP if last.code == "CAP-REACHED" else EXIT_RUN_FAILED
        elif last.kind == "gate_waiting":
            msg, fix = message_with_fix("GATE-REQUIRED")
            print(f"GATE-REQUIRED: {msg}\nFix: studio approve {run_id} {last.task_id} "
                  f"--reason \"...\"")
            exit_code = EXIT_GATE
        elif last.kind == "paused":
            print("Resume with:  studio resume " + run_id)
            exit_code = EXIT_RUN_FAILED if last.detail and "failed" in last.detail else EXIT_OK
        elif last.kind == "cancelled":
            exit_code = EXIT_CANCELLED
        elif last.kind == "task_failed":
            exit_code = EXIT_RUN_FAILED
    if args.json_out:
        state = engine.state(run_id)
        _print_json(state.to_obj())
    return exit_code


def cmd_list(args) -> int:
    engine = Engine(args.data_dir)
    rows = []
    for run_id in engine.store.run_ids():
        try:
            state = engine.state(run_id)
        except (ValueError, PlanFormatError):
            continue
        if args.status and state.status != args.status:
            continue
        rows.append({
            "run_id": run_id,
            "title": (state.title or state.plan.title)[:60],
            "status": state.status,
            "tasks": f"{sum(1 for t in state.tasks.values() if t.state == 'succeeded')}"
                     f"/{len(state.plan.tasks)}",
            "tokens": state.tokens_used(),
        })
    if args.json_out:
        _print_json(rows)
        return EXIT_OK
    for r in rows:
        print(f"{r['run_id']}  {r['status']:<10} tasks {r['tasks']:<7} "
              f"{r['tokens']:>9} tok  {r['title']}")
    if not rows:
        print("No runs yet. Describe a goal to create your first plan:  studio plan \"...\"")
    return EXIT_OK


def cmd_inspect(args) -> int:
    engine = Engine(args.data_dir)
    state = engine.state(args.run_id)
    if args.task:
        ts = state.task_state(args.task)
        if ts is None:
            print(f"no task {args.task} in run {args.run_id}")
            return EXIT_INVALID
        obj = ts.to_obj()
        if args.json_out:
            _print_json(obj)
            return EXIT_OK
        print(f"task {ts.task_id}: {ts.state}  attempts={len(ts.attempts)} "
              f"tool_calls={ts.tool_calls}")
        if ts.error_code:
            print(f"  error: {ts.error_code}")
        if ts.handoff:
            print(json.dumps(ts.handoff, ensure_ascii=False, indent=2))
        return EXIT_OK
    obj = state.to_obj()
    if args.json_out:
        _print_json(obj)
        return EXIT_OK
    print(f"run {state.run_id}: {state.status}")
    print(f"plan: {state.plan_id}  title: {state.title or state.plan.title}")
    for t in state.plan.tasks:
        ts = state.tasks.get(t.id)
        st = ts.state if ts else "pending"
        print(f"  {t.id:<8} {st:<18} {t.title}")
    return EXIT_OK


def cmd_resume(args) -> int:
    engine = Engine(args.data_dir)
    state = engine.state(args.run_id)
    if state.is_done():
        print(f"run {args.run_id} is already {state.status}")
        return EXIT_RUN_FAILED if state.status == "failed" else EXIT_OK
    store = Store(args.data_dir)
    registry, provider_id = _registry_for(store, args.provider)
    if not provider_id:
        print("No provider configured.")
        return EXIT_PROVIDER
    # Reuse the run's stored caps.
    caps = state.caps or {}
    args.cap_tokens = int(caps.get("tokens", 2_000_000))
    args.cap_wall_minutes = int(caps.get("wall_minutes", 480))
    print(f"Resuming {args.run_id}")
    return _drive(engine, store, args.run_id, registry, provider_id, args)


def cmd_gate(args, approve: bool) -> int:
    engine = Engine(args.data_dir)
    registry, provider_id = _registry_for(Store(args.data_dir), None)
    driver = engine.driver(args.run_id, registry,
                           {"default": (provider_id, "")}, DriverConfig())
    if approve:
        if not args.reason:
            print("--reason is required for gate decisions (spec 7.4)")
            return EXIT_INVALID
        driver.approve_gate(args.task_id, reason=args.reason)
        print(f"Gate approved for {args.task_id}. Resume with: studio resume {args.run_id}")
    else:
        driver.reject_gate(args.task_id, decision=args.decision, reason=args.reason or "")
        print(f"Gate rejected ({args.decision}).")
    return EXIT_OK


def cmd_task_control(args, action: str) -> int:
    engine = Engine(args.data_dir)
    registry, provider_id = _registry_for(Store(args.data_dir), None)
    driver = engine.driver(args.run_id, registry,
                           {"default": (provider_id, "")}, DriverConfig())
    if action == "retry":
        driver.retry(args.task_id)
        print(f"Task {args.task_id} queued for retry: studio resume {args.run_id}")
    elif action == "skip":
        skipped = driver.skip(args.task_id, cascade=not args.no_cascade)
        print("Skipped: " + ", ".join(skipped))
        print(f"Continue with: studio resume {args.run_id}")
    elif action == "rerun":
        stale = driver.rerun_from(args.task_id)
        print("Marked stale (results kept in history): " + ", ".join(stale))
        print(f"Continue with: studio resume {args.run_id}")
    return EXIT_OK


def cmd_export(args) -> int:
    store = Store(args.data_dir)
    # Capability gate first: a disabled module must produce the actionable
    # message even when other arguments are missing.
    ModuleRegistry().load("reporting", store.get_setting)
    if not getattr(args, "run_id", None):
        print("usage: studio export RUN_ID [--format markdown|json]")
        return EXIT_INVALID
    # Authoritative enablement boundary: reporting is independently
    # disableable, so the command loads it through the registry.
    reporting = ModuleRegistry().load("reporting", store.get_setting)
    engine = Engine(args.data_dir)
    state = engine.state(args.run_id)
    options = reporting.ExportOptions(ai_notice=not args.no_ai_notice,
                                       redact_prompts=args.redact_prompts)
    output = args.output or os.path.join(
        engine.store.export_dir(args.run_id),
        f"report.{ 'json' if args.format == 'json' else 'md'}")
    result = reporting.export_run(state, engine.store, fmt=args.format, options=options,
                                  output_path=output)
    print(f"Exported {result.size_bytes} bytes to {result.path}")
    return EXIT_OK


def cmd_modules(args) -> int:
    store = Store(args.data_dir)
    registry = ModuleRegistry()
    if args.modules_cmd in ("enable", "disable"):
        if not args.module_id:
            print("usage: studio modules enable|disable MODULE_ID")
            return EXIT_INVALID
        try:
            set_enabled(registry, args.module_id, args.modules_cmd == "enable",
                        store.get_setting, store.set_setting)
        except ValueError as exc:
            print(str(exc))
            return EXIT_INVALID
        state = "enabled" if args.modules_cmd == "enable" else "disabled"
        print(f"Module {args.module_id} {state} (takes effect on the next studio "
              f"command; data is never deleted).")
        return EXIT_OK
    # list (default)
    from las.app.capabilities import is_enabled
    rows = []
    for mid in registry.ids():
        m = registry.get(mid)
        rows.append({
            "id": mid, "version": m.version, "core": m.core,
            "enabled": is_enabled(registry, mid, store.get_setting),
            "summary": m.summary,
            "requires": m.requires, "optional": m.optional,
            "commands": [c.name for c in m.commands],
        })
    if args.json_out:
        _print_json(rows)
        return EXIT_OK
    for r in rows:
        flag = "core" if r["core"] else ("on " if r["enabled"] else "off")
        print(f"{r['id']:<16} {flag}  v{r['version']}  {r['summary']}")
    print("Enable or disable with:  studio modules enable|disable MODULE_ID")
    return EXIT_OK


def cmd_providers(args) -> int:
    store = Store(args.data_dir)
    if args.providers_cmd == "list":
        profiles = store.list_providers()
        if not profiles:
            print("No providers. Add one:  studio providers add --name my-server "
                  "--preset lmstudio")
            return EXIT_OK
        for p in profiles:
            print(f"{p.id:<16} {p.kind:<10} {p.base_url or '(default)'}"
                  + (f"  key: {p.auth_env}" if p.auth_env else ""))
        return EXIT_OK
    if args.providers_cmd == "add":
        preset = PRESETS.get(args.preset, PRESETS["generic"])
        if not args.name:
            print("--name is required (studio providers add --name my-server --preset ...)")
            return EXIT_INVALID
        profile = ProviderProfile(
            id=args.name, name=args.name, kind=args.preset,
            base_url=args.base_url or preset.default_base_url,
            auth_env=args.auth_env or "", wire=args.wire or "chat",
            created_at=_now())
        store.save_provider(profile)
        print(f"Provider {args.name} saved (preset {args.preset}, "
              f"{profile.base_url or 'no base url'}).")
        return EXIT_OK
    if args.providers_cmd == "detect":
        found = detect_servers()
        if not found:
            print("No servers found on ports 1234, 11434, 8080, 8000, 8888.")
            print("Start one (LM Studio, Ollama, llama.cpp, vLLM) or add a provider manually.")
            return EXIT_OK
        for f in found:
            print(f"port {f['port']}: {f['guess']} ({f['auth']})")
        return EXIT_OK
    if args.providers_cmd in ("test", None):
        profiles = store.list_providers()
        if args.name:
            profiles = [p for p in profiles if p.id == args.name or p.name == args.name]
        if not profiles:
            print("No providers to test." if not args.name else f"no provider {args.name}")
            return EXIT_INVALID
        results = {}
        for p in profiles:
            adapter = build_adapter(p)
            model = args.model
            if not model:
                try:
                    models = adapter.list_models()
                    model = models[0]["id"] if models else ""
                except ProviderError:
                    model = ""
            if not model:
                results[p.id] = {"error": "no models listed"}
                continue
            caps = probe_adapter(adapter, model, p.id)
            results[p.id] = caps.to_obj()
        if args.json_out:
            _print_json(results)
        else:
            for pid, caps in results.items():
                if "error" in caps:
                    print(f"{pid}: {caps['error']}")
                    continue
                print(f"{pid} / {caps['model']}")
                for key in ("reachable", "authenticated", "models_listed", "streaming",
                            "tool_calling", "json_schema", "token_counting", "router_mode"):
                    val = caps.get(key)
                    mark = "Pass" if val else ("Unknown" if val is None else "Fail")
                    print(f"  {key:<16} {mark}")
                print(f"  context_length  {caps.get('context_length') or 'Unknown'}")
        return EXIT_OK
    return EXIT_INVALID


def cmd_selftest(args) -> int:
    store = Store(args.data_dir)
    # Authoritative enablement boundary: selftest is independently
    # disableable; the default self-test additionally needs the mock.
    selftest_mod = ModuleRegistry().load("selftest", store.get_setting)
    adapter = None
    if args.provider:
        registry, pid = _registry_for(store, args.provider)
        adapter = registry.get(pid)
    else:
        require_module(ModuleRegistry(), "providers.mock", store.get_setting)
    result = selftest_mod.run_selftest(args.data_dir, adapter)
    if args.json_out:
        _print_json({"ok": result.ok, "run_id": result.run_id,
                     "steps": result.steps, "tokens_used": result.tokens_used,
                     "error": result.error})
    else:
        for step in result.steps:
            mark = "pass" if step["ok"] else "FAIL"
            print(f"  [{mark}] {step['name']} {step['detail']}")
        print(f"Self-test {'passed' if result.ok else 'failed'} "
              f"({result.tokens_used} tokens, run {result.run_id})")
        if result.error:
            print(f"  reason: {result.error}")
    return EXIT_OK if result.ok else EXIT_RUN_FAILED


def cmd_serve(args) -> int:
    store = Store(args.data_dir)
    api = ModuleRegistry().load("api", store.get_setting)
    return api.run_server(args.data_dir, port=args.port)


def cmd_verify(args) -> int:
    engine = Engine(args.data_dir)
    ok = engine.verify(args.run_id)
    print(f"event chain for {args.run_id}: {'verified' if ok else 'TAMPERED'}")
    return EXIT_OK if ok else EXIT_INTERNAL


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_templates(args) -> int:
    store = Store(args.data_dir)
    library = ModuleRegistry().load("library", store.get_setting)
    if args.templates_cmd == "save":
        import json as _json
        # The plan file may land in either positional slot depending on how
        # far the user got; accept both.
        plan_file = args.plan_file or args.template_id
        if not plan_file:
            print("usage: studio templates save PLAN_FILE --name NAME")
            return EXIT_INVALID
        with open(plan_file, encoding="utf-8") as fh:
            plan_obj = _json.load(fh)
        entry = library.save_template(store, plan_obj, args.name,
                                      args.description or "")
        print(f"Saved template {entry['id']} ({entry['task_count']} tasks)")
        return EXIT_OK
    if args.templates_cmd == "duplicate":
        entry = library.duplicate_template(store, args.template_id, args.new_name or "")
        print(f"Duplicated as {entry['id']}")
        return EXIT_OK
    if args.templates_cmd == "delete":
        try:
            library.delete_template(store, args.template_id)
        except library.LibraryError as exc:
            print(str(exc))
            return EXIT_INVALID
        print(f"Deleted {args.template_id}")
        return EXIT_OK
    if args.templates_cmd == "export":
        tpl = next((t for t in store.list_templates()
                    if t["id"] == args.template_id), None)
        if tpl is None:
            print(f"plan not found: {args.template_id}")
            return EXIT_INVALID
        import json as _json
        out = args.output or f"{tpl['id']}.json"
        with open(out, "w", encoding="utf-8") as fh:
            _json.dump(tpl, fh, ensure_ascii=False, indent=1)
        print(f"Exported template to {out}")
        return EXIT_OK
    # list (default)
    library.ensure_builtin_templates(store)
    rows = store.list_templates()
    if args.json_out:
        _print_json(rows)
        return EXIT_OK
    for t in rows:
        flag = "builtin" if t.get("builtin") else "user"
        variables = ", ".join(t.get("variables") or []) or "-"
        print(f"{t['id']:<28} {flag:<7} {t.get('task_count', 0)} tasks  "
              f"vars: {variables}  {t.get('name', '')}")
    return EXIT_OK


def cmd_roles(args) -> int:
    store = Store(args.data_dir)
    library = ModuleRegistry().load("library", store.get_setting)
    if args.roles_cmd == "save":
        import json as _json
        file = args.file or args.profile_id
        if not file:
            print("usage: studio roles save PROFILE_FILE")
            return EXIT_INVALID
        with open(file, encoding="utf-8") as fh:
            profile = _json.load(fh)
        try:
            entry = library.save_role_profile(store, profile)
        except library.LibraryError as exc:
            print(str(exc))
            return EXIT_INVALID
        print(f"Saved role profile {entry['id']} ({entry['role']})")
        return EXIT_OK
    if args.roles_cmd == "delete":
        if store.get_role_profile(args.profile_id) is None:
            print(f"plan not found: {args.profile_id}")
            return EXIT_INVALID
        store.delete_role_profile(args.profile_id)
        print(f"Deleted {args.profile_id}")
        return EXIT_OK
    rows = store.list_role_profiles()
    if args.json_out:
        _print_json(rows)
        return EXIT_OK
    for r in rows:
        print(f"{r['id']:<20} {r['role']:<9} {r['name']}  "
              f"tools: {', '.join(r.get('tools') or []) or '-'}")
    return EXIT_OK


def cmd_data(args) -> int:
    """Data management (SET-2): retention, prune, delete all data."""
    store = Store(args.data_dir)
    if args.data_cmd == "prune":
        retention = str(store.get_setting("data.retention", "keep forever"))
        if retention == "keep forever":
            print("Retention is 'keep forever'; nothing pruned. "
                  "Change it with: studio data prune --days N")
            return EXIT_OK
        days = args.days if args.days is not None else int(retention.split()[0])
        pruned = store.prune_runs(days)
        for run_id in pruned:
            print(f"Deleted run {run_id} (older than {days} days)")
        print(f"Pruned {len(pruned)} runs.")
        return EXIT_OK
    if args.data_cmd == "delete-all":
        if not getattr(args, "confirm", False):
            print("This deletes every run, workspace and export. Nothing else.\n"
                  "Re-run with --confirm to actually delete.")
            return EXIT_INVALID
        count = len(store.run_ids())
        store.delete_all_data()
        print(f"Deleted all data: {count} runs (providers, plans, templates, "
              f"role profiles and settings were kept).")
        return EXIT_OK
    # status (default)
    runs = store.run_ids()
    print(f"Data directory: {args.data_dir}")
    print(f"Runs: {len(runs)}")
    print(f"Retention: {store.get_setting('data.retention', 'keep forever')}")
    print("Commands: prune, delete-all --confirm")
    return EXIT_OK


def cmd_models(args) -> int:
    """Model lifecycle (PRV-8): list, load, unload."""
    store = Store(args.data_dir)
    registry, provider_id = _registry_for(store, args.provider)
    adapter = registry.get(provider_id)
    if args.models_cmd == "load":
        if not args.model:
            print("usage: studio models load --provider P --model M")
            return EXIT_INVALID
        adapter.preload(args.model)
        print(f"Loaded {args.model} on {provider_id}.")
        return EXIT_OK
    if args.models_cmd == "unload":
        if not args.model:
            print("usage: studio models unload --provider P --model M")
            return EXIT_INVALID
        adapter.unload(args.model)
        print(f"Unloaded {args.model} on {provider_id}.")
        return EXIT_OK
    # list
    for m in adapter.list_models():
        flag = "loaded" if m.get("loaded") else "available"
        print(f"{m['id']:<40} {flag}")
    return EXIT_OK


def cmd_diagnostics(args) -> int:
    store = Store(args.data_dir)
    diag = ModuleRegistry().load("diagnostics", store.get_setting)
    print("The diagnostics bundle contains:")
    for item in diag.diagnostics_items():
        print(f"  - {item}")
    print("Nothing is sent anywhere; the file is written locally.")
    from las.app.capabilities import is_enabled
    registry = ModuleRegistry()
    modules = [{"id": m.id, "version": m.version, "core": m.core,
                "enabled": is_enabled(registry, m.id, store.get_setting),
                "summary": m.summary}
               for m in registry.manifests.values()]
    caps = []
    import json as _json, os as _os
    caps_path = _os.path.join(args.data_dir, "capabilities.json")
    if _os.path.exists(caps_path):
        with open(caps_path, encoding="utf-8") as fh:
            caps = _json.load(fh)
    engine = Engine(args.data_dir)
    bundle = diag.build_diagnostics(store, engine, modules, caps)
    out = args.output or _os.path.join(args.data_dir, "diagnostics.json")
    with open(out, "w", encoding="utf-8") as fh:
        _json.dump(bundle, fh, ensure_ascii=False, indent=1)
    print(f"Wrote {out}")
    return EXIT_OK


# -- parser --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio", description="Local Agent Studio — local-first agent chains")
    parser.add_argument("--version", action="version", version=f"studio {__version__}")
    parser.add_argument("--data-dir", default=default_data_dir(),
                        help="data root (default: platform app data, or LAS_DATA_DIR)")
    # The same flag works after the subcommand (SUPPRESS keeps the value
    # given before the subcommand).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", parents=[common], help="Generate a plan from a goal")
    p.add_argument("goal")
    p.add_argument("-o", "--output", help="write the plan JSON to this path")
    p.add_argument("--granularity", choices=("coarse", "normal", "fine"), default="normal")
    p.add_argument("--max-tasks", type=int, default=30)
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", parents=[common], help="Run a plan or template")
    p.add_argument("plan")
    p.add_argument("--goal", help="override the goal (templates)")
    p.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--provider")
    p.add_argument("--planner-model")
    p.add_argument("--worker-model")
    p.add_argument("--on-failure", choices=("ask", "stop", "skip"), default="ask")
    p.add_argument("--cap-tokens", type=int, default=2_000_000)
    p.add_argument("--cap-wall-minutes", type=int, default=480)
    p.add_argument("--context-length", type=int, default=8192)
    p.add_argument("--pause-after", metavar="TASK_ID")
    p.add_argument("--enable-shell", action="store_true")
    p.add_argument("--enable-web", action="store_true")
    p.add_argument("--command-allowlist", help="comma-separated command names")
    p.add_argument("--web-domains", help="comma-separated domains")
    p.add_argument("--force", action="store_true", help="run despite lint warnings")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", parents=[common], help="List runs")
    p.add_argument("--status")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", parents=[common], help="Show a run")
    p.add_argument("run_id")
    p.add_argument("--task")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("resume", parents=[common], help="Resume a paused or failed run")
    p.add_argument("run_id")
    p.add_argument("--provider")
    p.add_argument("--planner-model")
    p.add_argument("--worker-model")
    p.add_argument("--on-failure", choices=("ask", "stop", "skip"), default="ask")
    p.add_argument("--pause-after", metavar="TASK_ID")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("approve", parents=[common], help="Approve a gate")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=lambda a: cmd_gate(a, approve=True))

    p = sub.add_parser("reject", parents=[common], help="Reject a gate")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--decision", choices=("stop", "skip", "edit"), default="stop")
    p.add_argument("--reason", default="")
    p.set_defaults(func=lambda a: cmd_gate(a, approve=False))

    for action, help_ in (("retry", "Retry a failed task"),
                          ("skip", "Skip a task (dependents are skipped too)"),
                          ("rerun", "Rerun from a task (later tasks go stale)")):
        p = sub.add_parser(action, parents=[common], help=help_)
        p.add_argument("run_id")
        p.add_argument("task_id")
        if action == "skip":
            p.add_argument("--no-cascade", action="store_true",
                           help="continue dependent tasks instead of skipping them")
        p.set_defaults(func=lambda a, act=action: cmd_task_control(a, act))

    p = sub.add_parser("export", parents=[common], help="Export a run")
    # run_id is optional at the parser level so the capability gate can
    # fire (with an actionable message) before usage errors are reported.
    p.add_argument("run_id", nargs="?")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--no-ai-notice", action="store_true")
    p.add_argument("--redact-prompts", action="store_true")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("providers", parents=[common], help="Manage providers")
    p.add_argument("providers_cmd", nargs="?", choices=("list", "add", "detect", "test"),
                   default="test")
    p.add_argument("--name", help="provider id (add)")
    p.add_argument("--preset", default="generic",
                   choices=tuple(PRESETS.keys()) + ("mock",),
                   help="server preset; 'mock' is the offline development provider")
    p.add_argument("--base-url")
    p.add_argument("--auth-env", help="env:VARNAME key reference")
    p.add_argument("--wire", choices=("chat", "responses"), default="chat")
    p.add_argument("--model", help="model id for the capability probe (test)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("selftest", parents=[common], help="Run the built-in three-task self-test")
    p.add_argument("--provider", help="use a configured provider instead of the mock")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("modules", parents=[common], help="List, enable or disable modules")
    p.add_argument("modules_cmd", nargs="?", choices=("list", "enable", "disable"),
                   default="list")
    p.add_argument("module_id", nargs="?", help="module id (enable/disable)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("templates", parents=[common], help="Manage plan templates (LIB-1)")
    p.add_argument("templates_cmd", nargs="?",
                   choices=("list", "save", "duplicate", "delete", "export"),
                   default="list")
    p.add_argument("template_id", nargs="?", help="template id")
    p.add_argument("plan_file", nargs="?", help="plan JSON file (save)")
    p.add_argument("--name", default="", help="template name (save)")
    p.add_argument("--description", default="", help="template description")
    p.add_argument("--new-name", dest="new_name", default="", help="duplicate name")
    p.add_argument("-o", "--output", default="", help="output file (export)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("roles", parents=[common], help="Manage role profiles (LIB-2)")
    p.add_argument("roles_cmd", nargs="?", choices=("list", "save", "delete"),
                   default="list")
    p.add_argument("profile_id", nargs="?", help="profile id (delete)")
    p.add_argument("file", nargs="?", help="profile JSON file (save)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("data", parents=[common], help="Data management (SET-2)")
    p.add_argument("data_cmd", nargs="?", choices=("status", "prune", "delete-all"),
                   default="status")
    p.add_argument("--days", type=int, default=None,
                   help="prune runs older than N days (overrides retention)")
    p.add_argument("--confirm", action="store_true",
                   help="required for delete-all")
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("models", parents=[common],
                       help="Model lifecycle: list, load, unload (PRV-8)")
    p.add_argument("models_cmd", nargs="?", choices=("list", "load", "unload"),
                   default="list")
    p.add_argument("--provider", default="", help="provider id or name")
    p.add_argument("--model", default="", help="model id (load/unload)")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("diagnostics", parents=[common],
                       help="Write a redacted diagnostics bundle (OBS-7)")
    p.add_argument("-o", "--output", default="", help="output file")
    p.set_defaults(func=cmd_diagnostics)

    p = sub.add_parser("serve", parents=[common],
                       help="Serve the engine HTTP API on loopback (spec 7.5)")
    p.add_argument("--port", type=int, default=0, help="port (default: random free)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("verify", parents=[common], help="Verify a run's event hash chain")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except (ModuleDisabledError, ModuleNotInstalledError) as exc:
        print(str(exc))
        return EXIT_INVALID
    except PlanFormatError as exc:
        print(f"PLAN-INVALID: {exc.detail}")
        return EXIT_INVALID
    except ProviderError as exc:
        msg, fix = message_with_fix(exc.code)
        print(f"{exc.code}: {msg}\nFix: {fix}")
        return EXIT_PROVIDER
    except TamperedLogError as exc:
        print(f"INTERNAL: {exc}")
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001
        print(f"INTERNAL: {type(exc).__name__}: {exc}")
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
