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


