"""Manifest for the api module (ADR-0002).

The M1 loopback engine API (spec 7.5): per-launch token, loopback-only,
origin-checked; the desktop shell (M2) and remote tooling are its clients.
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="api",
    version="1.0.0",
    parent=None,
    summary="Loopback HTTP engine API with per-launch token (spec 7.5, API-2)",
    contract="las.modules.api",
    core=False,
    requires=["execution", "planning", "providers", "storage"],
    optional=["reporting"],
    default_enabled=True,
    commands=[CommandSpec("serve", "Serve the engine HTTP API on loopback")],
    test_files=["tests/test_api.py"],
    compat_shims=[],
)
