"""Registry integrity tests (ADR-0002, architecture-enforcement section 4).

Structural, removal-proof assertions: no test hardcodes the module list,
so removing a module in the disposable removal test keeps this file green.
Discovery must stay complete, manifests consistent, and the module map in
sync with implemented reality.
"""

import importlib
import os
import unittest

from las.app.registry import ModuleRegistry, RegistryError, discover
from las.shared.module_contract import CommandSpec, ModuleManifest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest_paths() -> set[str]:
    """All manifest.py files under las/modules at any nesting depth."""
    root = os.path.join(REPO, "las", "modules")
    found = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        if "manifest.py" in filenames:
            found.add(os.path.relpath(dirpath, REPO)
                      .replace(os.sep, "/"))
    return found


class TestDiscovery(unittest.TestCase):
    def test_discovery_is_complete(self):
        registry = ModuleRegistry()
        expected_dirs = _manifest_paths()
        contract_dirs = {m.contract.replace(".", "/") for m in registry.manifests.values()}
        self.assertEqual(expected_dirs, contract_dirs,
                         "every manifest.py directory must be discovered, and "
                         "every discovered manifest must correspond to a directory")

    def test_every_manifest_has_essential_fields(self):
        for m in ModuleRegistry().manifests.values():
            self.assertTrue(m.id, "manifest id required")
            self.assertRegex(m.version, r"^\d+\.\d+\.\d+$")
            self.assertTrue(m.summary)
            self.assertTrue(m.contract)
            for c in m.commands:
                self.assertTrue(c.name)
                self.assertTrue(c.help)

    def test_contract_paths_importable(self):
        for m in ModuleRegistry().manifests.values():
            module = importlib.import_module(m.contract)
            self.assertTrue(hasattr(module, "__all__") or m.contract.startswith("las.shared"),
                            f"{m.contract} must define __all__ as its public surface")

    def test_manifest_test_files_exist(self):
        for m in ModuleRegistry().manifests.values():
            for tf in m.test_files:
                self.assertTrue(os.path.exists(os.path.join(REPO, tf)),
                                f"{m.id} declares missing test file {tf}")

    def test_manifest_compat_shims_exist(self):
        for m in ModuleRegistry().manifests.values():
            for shim in m.compat_shims:
                self.assertTrue(os.path.exists(os.path.join(REPO, shim)),
                                f"{m.id} declares missing shim {shim}")

    def test_module_map_lists_every_registered_module(self):
        with open(os.path.join(REPO, "MODULE_MAP.md"), encoding="utf-8") as fh:
            module_map = fh.read()
        for mid in ModuleRegistry().ids():
            self.assertIn(mid, module_map,
                          f"MODULE_MAP.md must list registered module {mid}")

    def test_cli_registers_enabled_module_commands(self):
        from las.app.cli import build_parser
        parser = build_parser()
        subchoices = set()
        for action in parser._actions:  # noqa: SLF001 - introspection is the point
            if action.choices is not None and action.dest == "command":
                subchoices = set(action.choices)
        registry = ModuleRegistry()
        for m in registry.manifests.values():
            for c in m.commands:
                self.assertIn(c.name, subchoices,
                              f"CLI must register command {c.name!r} contributed "
                              f"by module {m.id}")


class TestValidation(unittest.TestCase):
    def _manifest(self, **overrides) -> ModuleManifest:
        base = dict(id="x", version="1.0.0", contract="las.modules.x")
        base.update(overrides)
        return ModuleManifest(**base)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(RegistryError):
            ModuleRegistry([self._manifest(), self._manifest()])

    def test_missing_parent_rejected(self):
        with self.assertRaises(RegistryError):
            ModuleRegistry([self._manifest(id="child", parent="ghost")])

    def test_unknown_required_dependency_rejected(self):
        with self.assertRaises(RegistryError):
            ModuleRegistry([self._manifest(requires=["ghost"])])

    def test_missing_optional_dependency_allowed(self):
        # Optional dependencies may be absent: the declaring module handles
        # that case explicitly (selftest without providers.mock degrades).
        registry = ModuleRegistry([self._manifest(id="s", optional=["ghost"])])
        self.assertEqual(registry.ids(), ["s"])

    def test_dependency_cycle_rejected(self):
        a = self._manifest(id="a", requires=["b"])
        b = self._manifest(id="b", requires=["a"])
        with self.assertRaises(RegistryError):
            ModuleRegistry([a, b])

    def test_deep_nesting_is_allowed(self):
        # Audit item 14: trees nest recursively; only cycles (and
        # missing parents) are structural errors.
        p = self._manifest(id="p")
        c = self._manifest(id="c", parent="p")
        g = self._manifest(id="g", parent="c")
        gg = self._manifest(id="gg", parent="g")
        registry = ModuleRegistry([p, c, g, gg])
        self.assertEqual(sorted(registry.ids()),
                         ["c", "g", "gg", "p"])

    def test_parent_cycle_rejected(self):
        a = self._manifest(id="a", parent="b")
        b = self._manifest(id="b", parent="a")
        with self.assertRaises(RegistryError):
            ModuleRegistry([a, b])

    def test_self_parent_rejected(self):
        with self.assertRaises(RegistryError):
            ModuleRegistry([self._manifest(id="solo", parent="solo")])

    def test_missing_contract_rejected(self):
        with self.assertRaises(RegistryError):
            ModuleRegistry([self._manifest(contract="")])


class TestEnablement(unittest.TestCase):
    def test_core_modules_are_always_enabled(self):
        registry = ModuleRegistry()
        core_ids = [m.id for m in registry.manifests.values() if m.core]
        self.assertTrue(core_ids, "at least one core module must exist")
        for mid in core_ids:
            self.assertIn(mid, registry.enabled_ids(lambda key, default: False),
                          "a core module stays enabled regardless of settings")

    def test_disabling_a_module_excludes_it(self):
        registry = ModuleRegistry()
        non_core = [m.id for m in registry.manifests.values() if not m.core]
        self.assertTrue(non_core)

        def get_setting(key, default):
            return False if key.startswith("module.") else default

        enabled = registry.enabled_ids(get_setting)
        for mid in non_core:
            self.assertNotIn(mid, enabled)

    def test_load_refuses_disabled_module(self):
        from las.app.registry import ModuleDisabledError
        registry = ModuleRegistry()
        non_core = next(m.id for m in registry.manifests.values() if not m.core)
        with self.assertRaises(ModuleDisabledError):
            registry.load(non_core, lambda key, default: False)

    def test_load_unknown_module(self):
        from las.app.registry import ModuleNotInstalledError
        with self.assertRaises(ModuleNotInstalledError):
            ModuleRegistry().load("ghost", lambda key, default: default)


if __name__ == "__main__":
    unittest.main()
