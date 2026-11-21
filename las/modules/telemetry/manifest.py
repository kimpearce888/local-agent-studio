"""Manifest for the telemetry module (ADR-0002)."""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="telemetry",
    version="1.0.0",
    parent=None,
    summary="Optional OTLP export with pinned GenAI conventions (OBS-5), off by default",
    contract="las.modules.telemetry",
    core=False,
    requires=["execution", "storage"],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=["tests/test_telemetry.py"],
    compat_shims=[],
)
