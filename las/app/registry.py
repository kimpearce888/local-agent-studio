"""Module registry: discovery, validation, capability resolution (ADR-0002).

Discovery is a ``pkgutil`` scan for ``manifest.py`` files under
``las/modules/`` at ANY nesting depth (audit item 14: trees may nest
recursively; the parent chain must simply terminate - cycles are
rejected). Python
needs no generated references, so no central index exists and none is
allowed: the registry test keeps manifests, directories and this module in
sync. Enable/disable resolution happens at process start (no hot-unload in
Python); see ARCHITECTURE.md section 8.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable, Optional

from las.shared.module_contract import ModuleManifest

MODULES_PACKAGE = "las.modules"


class RegistryError(Exception):
    """A registry structure problem (duplicate id, missing parent, cycle)."""


class ModuleNotInstalledError(Exception):
    """A module id that no manifest provides."""

    def __init__(self, module_id: str):
        super().__init__(
            f"Module {module_id!r} is not installed. "
            f"Run 'studio modules list' to see the installed modules.")


class ModuleDisabledError(Exception):
    """A module is installed but disabled for this installation."""

    def __init__(self, module_id: str, fix: str = ""):
        self.module_id = module_id
        self.fix = fix or f"Run 'studio modules enable {module_id}' to enable it."
        super().__init__(f"Module {module_id!r} is disabled. {self.fix}")


def _load_manifest(dotted: str) -> Optional[ModuleManifest]:
    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError:
        return None
    manifest = getattr(module, "MODULE", None)
    if not isinstance(manifest, ModuleManifest):
        raise RegistryError(
            f"{dotted} exports no ModuleManifest named MODULE.")
    return manifest


def discover() -> list[ModuleManifest]:
    """Scan ``las/modules/**/manifest.py`` at any nesting depth."""
    manifests: list[ModuleManifest] = []
    package = importlib.import_module(MODULES_PACKAGE)

    def walk(path, prefix: str) -> None:
        for info in sorted(pkgutil.iter_modules(path),
                           key=lambda i: i.name):
            if not info.ispkg:
                continue
            dotted = f"{prefix}.{info.name}"
            manifest = _load_manifest(f"{dotted}.manifest")
            if manifest is not None:
                manifests.append(manifest)
            sub = importlib.import_module(dotted)
            walk(sub.__path__, dotted)

    walk(package.__path__, MODULES_PACKAGE)
    return manifests


class ModuleRegistry:
    """Validated, queryable set of module manifests."""

    def __init__(self, manifests: Optional[list[ModuleManifest]] = None):
        self.manifests: dict[str, ModuleManifest] = {}
        if manifests is None:
            manifests = discover()
        for m in manifests:
            if m.id in self.manifests:
                raise RegistryError(f"Duplicate module id {m.id!r}.")
            self.manifests[m.id] = m
        self.validate()

    # -- structure --------------------------------------------------------

    def validate(self) -> None:
        for m in self.manifests.values():
            if m.parent is not None:
                parent = self.manifests.get(m.parent)
                if parent is None:
                    raise RegistryError(
                        f"Module {m.id!r} declares parent {m.parent!r}, "
                        f"which has no manifest.")
                # Nested trees are fine (audit item 14); the parent
                # chain must terminate.  Walk it with a seen-set so
                # cycles are rejected instead of looping forever.
                seen = {m.id}
                up: Optional[str] = parent.parent
                steps = 0
                while up is not None:
                    if up in seen:
                        raise RegistryError(
                            f"Module {m.id!r} sits in a parent cycle at {up!r}.")
                    seen.add(up)
                    up = self.manifests[up].parent
                    steps += 1
                    if steps > 64:
                        raise RegistryError(
                            f"Module {m.id!r} nests deeper than 64 levels; "
                            f"this is almost certainly a configuration error.")
            for dep in m.requires:
                if dep not in self.manifests:
                    raise RegistryError(
                        f"Module {m.id!r} requires {dep!r}, which has no "
                        f"manifest; required dependencies must be installed.")
            # Optional dependencies may be absent: the declaring module
            # handles that case explicitly (RFI section 6).
            if not m.contract:
                raise RegistryError(f"Module {m.id!r} declares no contract path.")
        self._check_cycles()

    def _check_cycles(self) -> None:
        visited: dict[str, int] = {}

        def visit(mid: str, stack: list[str]) -> None:
            visited[mid] = 1
            stack.append(mid)
            deps = [d for d in self.manifests[mid].requires + self.manifests[mid].optional
                    if d in self.manifests]
            for dep in deps:
                if visited.get(dep) == 1:
                    raise RegistryError(
                        "Dependency cycle: " + " -> ".join(stack + [dep]))
                if visited.get(dep) != 2:
                    visit(dep, stack)
            stack.pop()
            visited[mid] = 2

        for mid in sorted(self.manifests):
            if visited.get(mid) != 2:
                visit(mid, [])

    # -- queries ----------------------------------------------------------

    def ids(self) -> list[str]:
        return sorted(self.manifests)

    def get(self, module_id: str) -> ModuleManifest:
        try:
            return self.manifests[module_id]
        except KeyError:
            raise ModuleNotInstalledError(module_id) from None

    def children(self, parent_id: str) -> list[ModuleManifest]:
        return [m for m in self.manifests.values() if m.parent == parent_id]

    def dependents(self, module_id: str, kind: str = "any") -> list[str]:
        """Modules that declare ``module_id`` in requires (kind='required'),
        optional (kind='optional') or either."""
        out = []
        for m in self.manifests.values():
            if module_id in m.requires and kind in ("any", "required"):
                out.append(m.id)
            elif module_id in m.optional and kind in ("any", "optional"):
                out.append(m.id)
        return sorted(out)

    # -- enablement -------------------------------------------------------

    def enabled_ids(self, get_setting: Callable[[str, object], object]) -> set[str]:
        """Resolve effective enablement from settings.

        ``get_setting(key, default)`` is a storage-provided callable. A core
        module is always enabled; every other module honours
        ``module.<id>.enabled`` (default: the manifest's default_enabled).
        """
        enabled: set[str] = set()
        for m in self.manifests.values():
            if m.core:
                enabled.add(m.id)
                continue
            key = f"module.{m.id}.enabled"
            value = get_setting(key, m.default_enabled)
            if value:
                enabled.add(m.id)
        return enabled

    def load(self, module_id: str, get_setting: Callable[[str, object], object]):
        """Import a module's contract, refusing disabled modules.

        This is the authoritative enablement boundary: hosts and commands
        obtain module capabilities through this method, never through direct
        imports of a module that can be disabled.
        """
        manifest = self.get(module_id)
        if module_id not in self.enabled_ids(get_setting):
            raise ModuleDisabledError(module_id)
        return importlib.import_module(manifest.contract)
