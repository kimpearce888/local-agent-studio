"""Host command module (S5b split); bodies moved verbatim from cli.py."""

from .common import (  # noqa: F401 - shared CLI context (S5b)
    EXIT_OK, EXIT_RUN_FAILED, EXIT_INVALID, EXIT_PROVIDER, EXIT_GATE,
    EXIT_CAP, EXIT_CANCELLED, EXIT_INTERNAL,
    default_data_dir, _print_json, _load_plan_arg, _registry_for, _now,
    argparse, json, os, sys, Optional, __version__,
    require_module, set_enabled, ModuleDisabledError, ModuleNotInstalledError,
    ModuleRegistry, lint_errors, lint, message, message_with_fix, Planner,
    PRESETS, ProviderRegistry, build_adapter, probe_adapter, detect_servers,
    ProviderError, DriverConfig, Engine, validate_plan,
    Plan, PlanFormatError, plan_variables, substitute_variables, task_from_obj,
    ProviderProfile, Store, TamperedLogError, KNOWN_TOOLS,
)
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
    args._config = _build_config(args)   # frozen into run.created (item 18)
    run_id = engine.create_run(plan, cap_tokens=args.cap_tokens,
                               cap_wall_minutes=args.cap_wall_minutes,
                               models=_models_map(registry, provider_id, args),
                               config=args._config)
    # PLN-7/TLS-8: the plan's attachments land in the run workspace so
    # tasks can read them with the jailed file tools.
    try:
        attachments_mod = ModuleRegistry().load("attachments", store.get_setting)
        copied = attachments_mod.copy_to_workspace(
            args.data_dir, plan.id, store.workspace_dir(run_id))
        if copied:
            print(f"Attachments in workspace: {', '.join(copied)}")
    except Exception:  # noqa: BLE001 - optional module, never blocks a run
        pass
    print(f"Run {run_id} started ({len(plan.tasks)} tasks)")
    return _drive(engine, store, run_id, registry, provider_id, args)


def _build_config(args) -> DriverConfig:
    return DriverConfig(
        failure_policy=getattr(args, "on_failure", None) or "ask",
        cap_tokens_run=int(getattr(args, "cap_tokens", 0)
                           or DEFAULTS["cap_tokens_run"]),
        cap_wall_minutes_run=float(
            getattr(args, "cap_wall_minutes", 0)
            or DEFAULTS["cap_wall_minutes_run"]),
        context_length=int(getattr(args, "context_length", 0)
                           or DEFAULTS["context_length"]),
        enable_shell=bool(getattr(args, "enable_shell", False)),
        enable_web=bool(getattr(args, "enable_web", False)),
        command_allowlist=[c for c in (getattr(args, "command_allowlist", "")
                                       or "").split(",") if c],
        web_domains=[d for d in (getattr(args, "web_domains", "")
                                 or "").split(",") if d])


def _models_map(registry: ProviderRegistry, provider_id: str, args) -> dict:
    worker = getattr(args, "worker_model", None) or ""
    if not worker:
        try:
            models = registry.get(provider_id).list_models()
            worker = models[0]["id"] if models else ""
        except (ProviderError, KeyError, IndexError):
            worker = ""
    models = {"default": (provider_id, worker)}
    if getattr(args, "planner_model", None):
        models["planner"] = (provider_id, args.planner_model)
    return models


def _drive(engine: Engine, store: Store, run_id: str, registry: ProviderRegistry,
           provider_id: str, args) -> int:
    frozen_models = getattr(args, "_frozen_models", None)
    if frozen_models:
        models = frozen_models
    else:
        models = _models_map(registry, provider_id, args)
    config = getattr(args, "_config", None) or _build_config(args)
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

    # Optional OTLP export (OBS-5): off unless configured; never fails the run.
    try:
        telemetry_mod = ModuleRegistry().load("telemetry", store.get_setting)
        telemetry_mod.maybe_export(store, run_id)
    except Exception:  # noqa: BLE001 - module disabled or unset
        pass

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
    # Derived summaries (M1 optimization): a run is replayed only when its
    # cached summary is stale, so listing stays cheap as history grows.
    rows = [{"run_id": s["run_id"], "title": s["title"],
             "status": s["status"],
             "tasks": f"{s['tasks_done']}/{s['tasks_total']}",
             "tokens": s["tokens"]}
            for s in engine.list_summaries(status=args.status or "")]
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
        task = next((t for t in state.plan.tasks if t.id == ts.task_id), None)
        print(f"task {ts.task_id}: {ts.state}  attempts={len(ts.attempts)} "
              f"tool_calls={ts.tool_calls}"
              + ("  (edited)" if ts.edited else ""))
        if task is not None and task.model:
            print(f"  model override: {task.model.get('provider_id', '')}/"
                  f"{task.model.get('model_id', '')}")
        if task is not None and task.params:
            print(f"  params: {json.dumps(task.params, ensure_ascii=False)}")
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
        bits = ""
        if t.model and t.model.get("provider_id"):
            bits += f"  [model: {t.model['provider_id']}/{t.model.get('model_id', '')}]"
        if ts is not None and ts.edited:
            bits += "  (edited)"
        print(f"  {t.id:<8} {st:<18} {t.title}{bits}")
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
    # Restore the frozen execution configuration (item 18): caps,
    # policies, permissions and models come from run.created; only
    # explicitly given flags override.
    first = store.first_event(args.run_id)
    frozen = (first.payload.get("config") if first else None) or {}
    frozen_models = dict((first.payload.get("models") if first else {}) or {})
    if getattr(args, "on_failure", None):
        frozen["failure_policy"] = args.on_failure
    if getattr(args, "worker_model", None) and frozen_models.get("default"):
        frozen_models["default"] = (frozen_models["default"][0],
                                    args.worker_model)
    if getattr(args, "planner_model", None):
        pid = frozen_models.get("default", (provider_id, ""))[0]
        frozen_models["planner"] = (pid, args.planner_model)
    args._config = DriverConfig.from_public_obj(frozen) if frozen else None
    args._frozen_models = frozen_models
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


def cmd_delete(args) -> int:
    """Delete one run and its workspace (SET-2). Requires --confirm."""
    store = Store(args.data_dir)
    if args.run_id not in store.run_ids():
        print(f"No run {args.run_id}.")
        return EXIT_INVALID
    if not args.confirm:
        print(f"This deletes run {args.run_id}, its workspace and exports.\n"
              f"Re-run with --confirm to actually delete.")
        return EXIT_INVALID
    store.delete_run(args.run_id)
    print(f"Deleted run {args.run_id}.")
    return EXIT_OK


def cmd_replan(args) -> int:
    """PLN-6: propose a revision of a run's remaining tasks, then apply the
    stored proposal after review. Pinned (user-edited) tasks survive."""
    store = Store(args.data_dir)
    engine = Engine(args.data_dir)
    try:
        kept, remaining = engine.replan_target(args.run_id)
    except ValueError as exc:
        print(str(exc))
        return EXIT_INVALID
    if args.apply:
        proposal = store.get_plan(args.apply)
        if proposal is None:
            print(f"No proposal plan {args.apply}.")
            return EXIT_INVALID
        try:
            outcome = engine.apply_replan(
                args.run_id, [task_from_obj(t) for t in proposal["tasks"]],
                reason=f"applied proposal {args.apply}")
        except ValueError as exc:
            print(str(exc))
            return EXIT_INVALID
        print(f"Replanned {args.run_id}: removed {outcome['removed'] or '[]'}, "
              f"pinned {outcome['pinned'] or '[]'}, added {outcome['added']}")
        return EXIT_OK
    # Propose: one planner call; the proposal is stored so the user approves
    # exactly the diff that was shown. (Applying needs no provider.)
    registry, provider_id = _registry_for(store, args.provider)
    if not provider_id:
        print("No provider configured. Add one first:  studio providers add ...")
        return EXIT_PROVIDER
    adapter = registry.get(provider_id)
    model = args.model
    if not model:
        models = adapter.list_models()
        model = models[0]["id"] if models else ""
    from las.modules.planning import Planner
    planner = Planner(adapter, model)
    remaining_tasks = [t for t in state.plan.tasks
                       if t.id not in set(kept)]
    result = planner.replan(state.plan.goal, state.plan, kept,
                            remaining_tasks, {})
    if result.error or not result.valid:
        print(f"Re-planning failed: {result.error or 'invalid proposal'}")
        for i in result.issues:
            print(f"  {i.rule}: {i.message}")
        return EXIT_PROVIDER
    store.save_plan(result.plan.to_obj())
    removed = [t.id for t in remaining_tasks]
    print(f"Proposal {result.plan.id} for run {args.run_id}:")
    print(f"  replaces remaining tasks: {', '.join(removed)}")
    print(f"  new tasks:")
    for t in result.plan.tasks:
        print(f"    + {t.id}: {t.title}")
    print(f"\nReview it, then apply with:\n"
          f"  studio replan {args.run_id} --apply {result.plan.id}")
    return EXIT_OK


def cmd_compare(args) -> int:
    """Compare two runs: plan diff and output diff (OBS-6)."""
    store = Store(args.data_dir)
    reporting = ModuleRegistry().load("reporting", store.get_setting)
    for run_id in (args.run_a, args.run_b):
        if run_id not in store.run_ids():
            print(f"No run {run_id}.")
            return EXIT_INVALID
    diff = reporting.compare_runs(store, args.run_a, args.run_b)
    if args.json_out:
        _print_json(diff)
        return EXIT_OK
    print(reporting.render_compare(diff))
    return EXIT_OK


def cmd_edit(args) -> int:
    """Edit a pending task (RUN-6): instructions, title, model override."""
    engine = Engine(args.data_dir)
    changes: dict = {}
    if args.instructions:
        changes["instructions"] = args.instructions
    if args.title:
        changes["title"] = args.title
    if args.model_provider or args.model_id:
        changes["model"] = {"provider_id": args.model_provider,
                            "model_id": args.model_id}
    if not changes:
        print("Nothing to change: use --instructions, --title or "
              "--model-provider/--model-id.")
        return EXIT_INVALID
    try:
        engine.edit_task(args.run_id, args.task_id, changes, args.reason)
    except ValueError as exc:
        print(str(exc))
        return EXIT_INVALID
    print(f"Edited task {args.task_id} in {args.run_id}: "
          f"{', '.join(sorted(changes))}")
    if args.reason:
        print(f"Reason: {args.reason}")
    return EXIT_OK


def cmd_verify(args) -> int:
    engine = Engine(args.data_dir)
    ok = engine.verify(args.run_id)
    print(f"event chain for {args.run_id}: {'verified' if ok else 'TAMPERED'}")
    return EXIT_OK if ok else EXIT_INTERNAL


