"""Manifest for the diagnostics module (ADR-0002)."""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="diagnostics",
    version="1.0.0",
    parent=None,
    summary="Redacted local diagnostics bundle (OBS-7, spec S13)",
    contract="las.modules.diagnostics",
    core=False,
    requires=["execution", "storage"],
    optional=[],
    default_enabled=True,
    commands=[CommandSpec("diagnostics", "Write a redacted diagnostics bundle")],
    test_files=["tests/test_diagnostics.py"],
    compat_shims=[],
)
