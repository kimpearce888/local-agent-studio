"""Capability policy: enable/disable of modules, enforced at boundaries.

Enablement is stored in settings (``module.<id>.enabled``) and takes effect
at the next process start (Python cannot hot-unload code). Disabling a core
module, or a module that others require, is refused with an actionable
error naming the dependents (GAAPS/RFI section 7). Disabling never deletes
data (RFI section 9).
"""

from __future__ import annotations

from typing import Callable

from las.app.registry import ModuleDisabledError, ModuleRegistry
from las.shared.module_contract import ModuleManifest

SettingGetter = Callable[[str, object], object]
SettingSetter = Callable[[str, object], None]


def module_setting_key(module_id: str) -> str:
    return f"module.{module_id}.enabled"


def is_enabled(registry: ModuleRegistry, module_id: str,
               get_setting: SettingGetter) -> bool:
    manifest = registry.get(module_id)
    if manifest.core:
        return True
    return bool(get_setting(module_setting_key(module_id), manifest.default_enabled))


def require_module(registry: ModuleRegistry, module_id: str,
                   get_setting: SettingGetter) -> ModuleManifest:
    """Raise ModuleDisabledError unless the module is enabled.

    The authoritative boundary check for commands and engine entry points.
    """
    manifest = registry.get(module_id)
    if not is_enabled(registry, module_id, get_setting):
        raise ModuleDisabledError(module_id)
    return manifest


def set_enabled(registry: ModuleRegistry, module_id: str, enabled: bool,
                get_setting: SettingGetter, set_setting: SettingSetter) -> None:
    """Enable or disable a module, refusing unsafe transitions.

    Refusals (core modules; modules with required dependents) raise
    ValueError with an actionable message. Optional dependents degrade by
    declaration and are listed in the returned warning list.
    """
    manifest = registry.get(module_id)
    if manifest.core:
        raise ValueError(
            f"Module {module_id!r} is required by the application and cannot "
            f"be disabled.")
    if not enabled:
        required_dependents = registry.dependents(module_id, kind="required")
        if required_dependents:
            raise ValueError(
                f"Module {module_id!r} cannot be disabled because "
                f"{', '.join(required_dependents)} requires it. Disable those "
                f"modules first, or keep {module_id!r} enabled.")
    set_setting(module_setting_key(module_id), bool(enabled))
