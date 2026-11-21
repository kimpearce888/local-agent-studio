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
    Plan, PlanFormatError, plan_variables, substitute_variables, task_from_obj,
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


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


