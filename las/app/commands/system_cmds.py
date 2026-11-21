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


