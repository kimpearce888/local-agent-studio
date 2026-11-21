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
    attachments_block = ""
    imported_attachments = 0
    if getattr(args, "attach", None):
        try:
            attachments_mod = ModuleRegistry().load(
                "attachments", Store(args.data_dir).get_setting)
        except Exception as exc:  # noqa: BLE001 - disabled module
            print(str(exc))
            return EXIT_INVALID
        entries = []
        for path_arg in args.attach:
            try:
                # The plan id is not known before planning; import under a
                # staging id and re-key the directory afterwards.
                entries.append(attachments_mod.import_file(
                    path_arg, args.data_dir, "_incoming"))
            except attachments_mod.AttachmentError as exc:
                print(str(exc))
                return EXIT_INVALID
        attachments_block = attachments_mod.manifest_block(entries)
        imported_attachments = len(entries)
    result = planner.plan(args.goal, {
        "granularity": args.granularity, "max_tasks": args.max_tasks},
        attachments_block=attachments_block)
    if imported_attachments and (result.error or result.plan is None):
        # Planning failed: remove the staged files so nothing lingers.
        import os as __os, shutil as __shutil
        __shutil.rmtree(__os.path.join(args.data_dir, "attachments",
                                       "_incoming"), ignore_errors=True)
    if imported_attachments and result.plan is not None:
        import os as _os, shutil as _shutil
        src_dir = _os.path.join(args.data_dir, "attachments", "_incoming")
        dest_dir = _os.path.join(args.data_dir, "attachments", result.plan.id)
        if _os.path.isdir(src_dir):
            _shutil.rmtree(dest_dir, ignore_errors=True)
            _shutil.move(src_dir, dest_dir)
        print(f"Attached {imported_attachments} file(s) to plan {result.plan.id}.")
    if not result.error and result.plan is not None:
        pending = globals().pop("_pending_attachments", [])
        if pending:
            import os as _os, shutil as _shutil
            src_dir = _os.path.join(args.data_dir, "attachments", "_incoming")
            dest_dir = _os.path.join(args.data_dir, "attachments", result.plan.id)
            if _os.path.isdir(src_dir):
                _shutil.rmtree(dest_dir, ignore_errors=True)
                _shutil.move(src_dir, dest_dir)
            print(f"Attached {len(pending)} file(s) to plan {result.plan.id}.")
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


