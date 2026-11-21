"""Executable architecture rules (ADR-0003) — stdlib AST checker.

Runs inside the normal unittest suite, so every `python -m unittest
discover` and every CI job enforces the boundaries in
DEPENDENCY_RULES.md. A rule that is never executed is not enforcement.

Rules
  R1  kernel purity     — las/shared/** imports nothing inside las except
                          las.shared.*
  R2  host is the top   — las/modules/** never imports las.app
  R3  facade-only       — a module imports another module only through its
                          package facade (or a registered child facade),
                          never a private file
  R4  private names     — no cross-module import of underscore names
  R5  declaration       — every canonical cross-module import is declared
                          in the importer's manifest (requires/optional)
  R6  forbidden edges   — manifest dependency edges banned by
                          DEPENDENCY_RULES.md section 4
  R7  no cycles         — the canonical import graph is acyclic
  R8  shim purity       — transition-era shims contain only re-exports of
                          canonical targets

Transition exception (DEPENDENCY_RULES.md section 6, review condition:
step S7): files under las/modules/** may still import documented legacy
paths while migration steps S2-S6 are in flight; S4 rewrites internal
imports to canonical paths and this allowlist shrinks to empty at S7.
"""

import ast
import os
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAS = os.path.join(REPO, "las")

# Documented transition exception (DEPENDENCY_RULES section 6). S4 rewrote
# every import inside las/modules and las/app to canonical paths, so this
# set is now EMPTY: legacy imports in the canonical zone are violations.
# Only the test suite still uses legacy paths (via the shims) until S7.
LEGACY_ALLOWED_IN_MODULES: set[str] = set()

# Pure re-export shims: files that exist only to keep old import paths
# alive. They may import canonical targets only, and nothing else.
PURE_SHIMS = {
    "las/messages.py", "las/export.py", "las/selftest.py", "las/planner.py",
    "las/linter.py", "las/context.py", "las/tools.py", "las/workspace.py",
    "las/store.py", "las/scheduler.py", "las/runstate.py", "las/cli.py",
    "las/schema/__init__.py", "las/schema/plan.py", "las/schema/handoff.py",
    "las/providers/__init__.py", "las/providers/base.py", "las/providers/mock.py",
    "las/providers/openai_compat.py", "las/providers/presets.py",
    "las/providers/probe.py",
}

# Manifest dependency edges banned outright (DEPENDENCY_RULES section 4).
FORBIDDEN_EDGES = {
    "providers": {"storage", "execution", "context"},
    "storage": {"providers", "planning", "planquality", "execution", "context",
                "handoffs", "security", "reporting", "selftest",
                "providers.mock"},
}


def rel(path: str, root: str = REPO) -> str:
    return os.path.relpath(path, root)


def classify(path: str, root: str = REPO) -> str:
    """kernel | module:<id> | host | shim | legacy"""
    r = rel(path, root).replace(os.sep, "/")
    if r.startswith("las/shared/"):
        return "kernel"
    if r.startswith("las/modules/"):
        bits = r[len("las/modules/"):].split("/")
        mod = bits[0]
        if len(bits) >= 2 and os.path.isdir(os.path.join(root, "las", "modules",
                                                          mod, bits[1])):
            return f"module:{mod}.{bits[1]}"
        return f"module:{mod}"
    if r in PURE_SHIMS:
        return "shim"
    if r.startswith("las/app/") or r in ("las/cli.py", "las/__init__.py",
                                         "las/__main__.py"):
        return "host"
    return "legacy"


def module_id_of(classification: str) -> str | None:
    return classification.split(":", 1)[1] if classification.startswith("module:") else None


def imports_of(path: str) -> list[tuple[str, list[str], int]]:
    """(module, imported_names, lineno) for every las import in a file."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "las" or alias.name.startswith("las."):
                    found.append((alias.name, [alias.asname or alias.name.split(".")[-1]],
                                  node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                continue  # relative imports: handled via the file's own module
            if mod == "las" or mod.startswith("las."):
                found.append((mod, [a.name for a in node.names], node.lineno))
    return found


def _registry_contracts() -> dict[str, str]:
    """contract path -> module id, from the live registry."""
    from las.app.registry import ModuleRegistry
    return {m.contract: m.id for m in ModuleRegistry().manifests.values()}


def _manifest_deps() -> dict[str, set[str]]:
    from las.app.registry import ModuleRegistry
    out = {}
    for m in ModuleRegistry().manifests.values():
        out[m.id] = set(m.requires) | set(m.optional)
    return out


def check_file(path: str, contracts: dict[str, str] | None = None,
               deps: dict[str, set[str]] | None = None,
               root: str = REPO) -> list[str]:
    """Violations in one file. Empty list = clean."""
    contracts = contracts if contracts is not None else _registry_contracts()
    deps = deps if deps is not None else _manifest_deps()
    kind = classify(path, root)
    own = module_id_of(kind)
    violations: list[str] = []

    for module, names, lineno in imports_of(path):
        # R1 kernel purity
        if kind == "kernel" and not module.startswith("las.shared"):
            violations.append(
                f"R1 kernel purity: {rel(path, root)}:{lineno} imports {module}; "
                f"the shared kernel imports nothing internal")
            continue
        # R8 shim purity: only canonical re-exports
        if kind == "shim" and not (module.startswith("las.shared")
                                   or module.startswith("las.modules")):
            violations.append(
                f"R8 shim purity: {rel(path, root)}:{lineno} imports {module}; a shim "
                f"may only re-export las.shared or las.modules targets")
            continue
        if kind == "legacy":
            continue  # pre-S4 code, governed by the suite; moves in S4
        # From here: host and module files.
        target_module_id = None
        if module.startswith("las.modules"):
            target_module_id = _target_module_id(module, contracts)
        # R2 host is the top
        if module == "las.app" or module.startswith("las.app."):
            if kind.startswith("module:"):
                violations.append(
                    f"R2 host is never a dependency: {rel(path, root)}:{lineno} "
                    f"imports {module}")
                continue
        # R3/R5 canonical cross-module imports
        if kind.startswith("module:") and module.startswith("las.modules"):
            if target_module_id is None or target_module_id == own:
                # own module (any depth) or unknown: unknown handled below
                if target_module_id is None:
                    violations.append(
                        f"R3 facade-only: {rel(path, root)}:{lineno} imports {module}; "
                        f"use the target module's package facade")
                continue
            deep = _is_deep_import(module, contracts)
            if deep:
                violations.append(
                    f"R3 facade-only: {rel(path, root)}:{lineno} imports private file "
                    f"{module}; import the module facade instead")
                continue
            if own is not None and target_module_id not in deps.get(own, set()):
                violations.append(
                    f"R5 declaration: {rel(path, root)}:{lineno} imports "
                    f"{target_module_id!r} which is not declared in module "
                    f"{own!r}'s manifest (requires/optional)")
        # R3 for host: no deep imports into module private files
        if kind == "host" and module.startswith("las.modules"):
            if target_module_id is None:
                violations.append(
                    f"R3 facade-only: {rel(path, root)}:{lineno} imports {module}; "
                    f"use the module facade")
                continue
            if _is_deep_import(module, contracts):
                violations.append(
                    f"R3 facade-only: {rel(path, root)}:{lineno} imports private file "
                    f"{module}; import the module facade instead")
        # R4 private names (cross-module, canonical or legacy targets)
        if own is not None or kind in ("host", "shim"):
            for name in names:
                if name.startswith("_") and module != "las":
                    violations.append(
                        f"R4 private name: {rel(path, root)}:{lineno} imports "
                        f"{name!r} from {module}")
        # The root package exposes only distribution metadata (APP_NAME,
        # __version__) and is always importable.
        if module == "las":
            continue
        # Legacy transition exception for module files
        if kind.startswith("module:") and not module.startswith(("las.shared",
                                                                 "las.modules")):
            if module not in LEGACY_ALLOWED_IN_MODULES:
                violations.append(
                    f"transition: {rel(path, root)}:{lineno} imports {module}; legacy "
                    f"path is not in the documented exception list "
                    f"(DEPENDENCY_RULES section 6)")
    return violations


def _target_module_id(module: str, contracts: dict[str, str]) -> str | None:
    """Module id a las.modules.* import refers to, or None if unknown."""
    if module in contracts:
        return contracts[module]
    # A registered child: las.modules.providers.mock
    parts = module.split(".")
    if len(parts) == 4:
        candidate = f"{parts[2]}.{parts[3]}"
        if candidate in contracts.values():
            return candidate
    return None


def _is_deep_import(module: str, contracts: dict[str, str]) -> bool:
    """True when the import reaches below a facade or child facade."""
    parts = module.split(".")
    if module in contracts:
        return False
    if len(parts) == 4 and f"{parts[2]}.{parts[3]}" in contracts.values():
        return False
    return True


def check_dependencies() -> list[str]:
    """R6: forbidden manifest dependency edges."""
    from las.app.registry import ModuleRegistry
    violations = []
    for m in ModuleRegistry().manifests.values():
        banned = FORBIDDEN_EDGES.get(m.id, set())
        for dep in m.requires + m.optional:
            if dep in banned or "*" in banned:
                violations.append(
                    f"R6 forbidden edge: module {m.id!r} declares dependency "
                    f"{dep!r} (DEPENDENCY_RULES section 4)")
    return violations


def module_graph() -> dict[str, set[str]]:
    """Canonical import graph between modules/kernel/host (R7)."""
    graph: dict[str, set[str]] = {}
    for dirpath, dirnames, filenames in os.walk(LAS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            kind = classify(path, LAS)
            if kind == "legacy":
                continue
            node = kind if kind != "host" else "host"
            graph.setdefault(node, set())
            for module, _names, _lineno in imports_of(path):
                if not module.startswith("las."):
                    continue
                if module.startswith("las.shared"):
                    graph[node].add("shared")
                elif module.startswith("las.modules"):
                    parts = module.split(".")
                    target = parts[2] if len(parts) >= 3 else module
                    if len(parts) == 4 and f"{parts[2]}.{parts[3]}" in _registry_contracts().values():
                        target = f"{parts[2]}.{parts[3]}"
                    graph[node].add(f"module:{target}")
                elif module.startswith("las.app") and kind.startswith("module:"):
                    graph[node].add("host")
    return graph


def check_cycles() -> list[str]:
    graph = module_graph()
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, ()):
            if state.get(dep) == 1:
                return stack + [dep]
            if state.get(dep) != 2:
                found = visit(dep, stack)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if state.get(node) != 2:
            cycle = visit(node, [])
            if cycle:
                return [f"R7 cycle: {' -> '.join(cycle)}"]
    return []


def check_all(root: str = LAS) -> list[str]:
    violations: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            try:
                violations.extend(check_file(os.path.join(dirpath, name), root=root))
            except SyntaxError as exc:
                violations.append(f"parse error (fails closed): {exc}")
    violations.extend(check_dependencies())
    violations.extend(check_cycles())
    return violations


class TestArchitectureRules(unittest.TestCase):
    """The repository must satisfy every rule (ADR-0003)."""

    def test_no_violations(self):
        violations = check_all()
        self.assertEqual(violations, [],
                         "architecture violations:\n  " + "\n  ".join(violations))

    def test_legacy_exception_list_is_empty(self):
        # S4 rewrote every canonical-zone import; from here until S7 no
        # legacy import path may appear inside las/modules or las/app.
        self.assertEqual(LEGACY_ALLOWED_IN_MODULES, set())


class TestRuleMutations(unittest.TestCase):
    """Each rule must fire on a deliberate violation (fails closed)."""

    def _write(self, tree: dict[str, str]) -> str:
        tmp = tempfile.mkdtemp(prefix="las-arch-")
        self.addCleanup(_rmtree, tmp)
        for relpath, content in tree.items():
            path = os.path.join(tmp, relpath)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        return tmp

    def _check(self, tree: dict[str, str], rule: str) -> None:
        root = self._write(tree)
        contracts = {"las.modules.a": "a", "las.modules.a.b": "a.b",
                     "las.modules.c": "c"}
        deps = {"a": set(), "a.b": set(), "c": set()}
        for relpath, _content in tree.items():
            violations = check_file(os.path.join(root, relpath),
                                    contracts=contracts, deps=deps, root=root)
            if rule in " ".join(violations):
                return
        self.fail(f"rule {rule} did not fire for the mutation")

    def test_r1_kernel_purity_fires(self):
        self._check({
            "las/shared/bad.py": "import las.store\n",
        }, "R1")

    def test_r2_host_never_a_dependency_fires(self):
        self._check({
            "las/modules/a/__init__.py": "from las.app.registry import x\n",
        }, "R2")

    def test_r3_deep_import_fires(self):
        self._check({
            "las/modules/c/__init__.py":
                "from las.modules.a.private import thing\n",
        }, "R3")

    def test_r4_private_name_fires(self):
        self._check({
            "las/modules/c/__init__.py":
                "from las.modules.a import _secret\n",
        }, "R4")

    def test_r5_undeclared_dependency_fires(self):
        self._check({
            "las/modules/c/__init__.py": "import las.modules.a\n",
        }, "R5")

    def test_r8_shim_purity_fires(self):
        root = self._write({"las/providers/base.py":
                            "from las.scheduler import Engine\n"})
        contracts = {"las.modules.a": "a"}
        violations = check_file(os.path.join(root, "las/providers/base.py"),
                                contracts=contracts, deps={}, root=root)
        self.assertTrue(any("R8" in v for v in violations), violations)

    def test_transition_unknown_legacy_path_fires(self):
        self._check({
            "las/modules/a/__init__.py": "import las.ghost\n",
        }, "transition")


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
