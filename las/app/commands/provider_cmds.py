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


