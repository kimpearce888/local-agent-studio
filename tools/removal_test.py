#!/usr/bin/env python3
"""Disposable module removal test (RFI section 11.7, ADR-0002).

Copies the repository to a temporary directory, removes a module (its
directory, manifest-declared test files and compat shims, plus test files
that import it), then verifies on the copy only:

1. the registry still discovers and validates cleanly;
2. the remaining unittest suite passes;
3. no stale references to the removed module remain under las/ or tests/.

The working tree is never touched. Exit code 0 = removal verified.

Usage: python3 tools/removal_test.py MODULE_ID [--suite tests.test_plan_validation ...]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE = shutil.ignore_patterns(
    "__pycache__", ".git", "*.pyc", ".venv", "dist",
    # build artifacts of the Rust workspace (multi-GB) and caches:
    # the copy only ever needs the tracked Python sources
    "target", "node_modules", ".mypy_cache", ".pytest_cache",
    ".cargo", "appdir", "appimage-verify")


def manifest_files_of(text: str) -> list[str]:
    """Test files declared in a manifest source text."""
    import re
    m = re.search(r"test_files=\[(.*?)\]", text, re.DOTALL)
    if not m:
        return []
    return re.findall(r'["\']([^"\']+)["\']', m.group(1))


def importers_in_copy(copy: str, needles: list[str], roots=("las", "tests"),
                      optional_marker: str = "") -> list[str]:
    """Files under roots (relative) whose *imports* reference a needle.

    Test files are matched on actual import statements (AST), so merely
    mentioning a path string (for example the checker's own shim table)
    does not count as a dependency. Files under ``las/`` are matched on
    any textual reference, skipping lines tagged with ``optional_marker``
    (``# optional-dep: MODULE_ID``), the documented graceful-degradation
    sites.
    """
    import ast as _ast
    affected: list[str] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(os.path.join(copy, root)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                if root == "tests":
                    with open(path, encoding="utf-8") as fh:
                        tree = _ast.parse(fh.read(), filename=path)
                    modules = []
                    for node in _ast.walk(tree):
                        if isinstance(node, _ast.Import):
                            modules.extend(a.name for a in node.names)
                        elif isinstance(node, _ast.ImportFrom) and node.module and not node.level:
                            modules.append(node.module)
                    if any(m == n or m.startswith(n + ".")
                           for m in modules for n in needles):
                        affected.append(os.path.relpath(path, copy))
                else:
                    with open(path, encoding="utf-8") as fh:
                        lines = fh.readlines()
                    hit = False
                    for line in lines:
                        if optional_marker and optional_marker in line:
                            continue
                        if any(needle in line for needle in needles):
                            hit = True
                            break
                    if hit:
                        affected.append(os.path.relpath(path, copy))
    return affected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module_id")
    args = parser.parse_args()

    sys.path.insert(0, REPO)
    from las.app.registry import ModuleRegistry  # noqa: E402

    registry = ModuleRegistry()
    try:
        manifest = registry.get(args.module_id)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 2
    if manifest.core:
        print(f"FAIL: {args.module_id!r} is a core module and is not removable.")
        return 2

    module_dir = os.path.join(REPO, manifest.contract.replace(".", "/"))
    dotted = lambda p: p[:-3].replace("/", ".")  # noqa: E731 - path -> module
    needles = [manifest.contract, *manifest.compat_shims,
               *(dotted(s) for s in manifest.compat_shims)]

    with tempfile.TemporaryDirectory(prefix="las-removal-") as tmp:
        copy = os.path.join(tmp, "repo")
        shutil.copytree(REPO, copy, ignore=IGNORE)

        # 1. Remove the module directory.
        rel_dir = os.path.relpath(module_dir, REPO)
        shutil.rmtree(os.path.join(copy, rel_dir))
        print(f"removed {rel_dir}/")

        # 2. Remove declared test files and compat shims.
        for rel in [*manifest.test_files, *manifest.compat_shims]:
            path = os.path.join(copy, rel)
            if os.path.exists(path):
                os.remove(path)
                print(f"removed {rel}")

        # 3. Remove test files that import the module, transitively: a test
        #    file that imports a deleted file (e.g. the shared mock harness
        #    in tests/util.py) cannot run either and belongs to the cascade.
        deleted_tests: set[str] = set()
        while True:
            needles_now = list(needles) + [f for f in deleted_tests]
            affected = importers_in_copy(copy, needles_now, roots=("tests",))
            affected = [a for a in affected if a not in deleted_tests]
            if not affected:
                break
            for rel in affected:
                os.remove(os.path.join(copy, rel))
                deleted_tests.add(rel)                                 # tests/util.py
                deleted_tests.add(rel[:-3].replace("/", "."))          # tests.util
                print(f"removed {rel} (imports the removed module or a deleted test file)")

        # 3b. Surviving manifests drop test_files entries whose files were
        # removed with a fixture module (a build without that module ships
        # without those integration tests, and the manifest says so).
        for mdir in [d for d in os.listdir(os.path.join(copy, "las", "modules"))
                     if os.path.isdir(os.path.join(copy, "las", "modules", d))]:
            mpath = os.path.join(copy, "las", "modules", mdir, "manifest.py")
            if not os.path.exists(mpath):
                continue
            with open(mpath, encoding="utf-8") as fh:
                mtext = fh.read()
            for tf in manifest_files_of(mtext):
                if not os.path.exists(os.path.join(copy, tf)):
                    for q in ('"', "'"):
                        mtext = mtext.replace(f"{q}{tf}{q}, ", "").replace(f"{q}{tf}{q}", "")
                    print(f"adjusted {mdir} manifest: dropped test file {tf} "
                          f"(removed with a fixture module)")
                    with open(mpath, "w", encoding="utf-8") as fh:
                        fh.write(mtext)

        # 4. Registry must still discover and validate cleanly.
        proc = subprocess.run(
            [sys.executable, "-c",
             "from las.app.registry import ModuleRegistry; "
             "r = ModuleRegistry(); "
             "assert '" + manifest.id + "' not in r.manifests; "
             "print('registry ok:', ', '.join(r.ids()))"],
            cwd=copy, capture_output=True, text=True)
        if proc.returncode != 0:
            print("FAIL: registry broken after removal:")
            print(proc.stdout + proc.stderr)
            return 1
        print(proc.stdout.strip())

        # 5. No stale references under las/ (tests were already handled).
        marker = f"optional-dep: {manifest.id}"
        stale = importers_in_copy(copy, needles, roots=("las",), optional_marker=marker)
        if stale:
            print("FAIL: stale references to the removed module remain:")
            for rel in stale:
                print(f"  {rel}")
            return 1
        print("no stale references under las/")

        # 6. Remaining suite passes on the copy.
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=copy, capture_output=True, text=True)
        tail = proc.stderr.strip().splitlines()[-2:] if proc.stderr.strip() else []
        print("suite on copy:", "; ".join(tail) or proc.stdout.strip()[-200:])
        if proc.returncode != 0:
            print("FAIL: the remaining suite does not pass after removal")
            print(proc.stderr[-3000:])
            return 1

    print(f"REMOVAL VERIFIED for {args.module_id!r}: registry clean, no stale "
          f"references, remaining suite green. The working tree was not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
