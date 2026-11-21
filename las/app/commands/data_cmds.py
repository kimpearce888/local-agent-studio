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


