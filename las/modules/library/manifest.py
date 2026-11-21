"""Manifest for the library module (ADR-0002)."""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="library",
    version="1.0.0",
    parent=None,
    summary="Reusable templates and role profiles (LIB-1, LIB-2, spec S10)",
    contract="las.modules.library",
    core=False,
    requires=["planquality", "storage"],
    optional=[],
    default_enabled=True,
    commands=[
        CommandSpec("templates", "List, save, duplicate, export or delete plan templates"),
        CommandSpec("roles", "List, save or delete role profiles"),
    ],
    test_files=["tests/test_library.py"],
    compat_shims=[],
)
