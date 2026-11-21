"""Manifest for the providers.mock child module (ADR-0002).

The mock is a registered child of providers: independently disableable and
excludable from production builds. ``selftest`` optionally depends on it;
engine tests script it as the deterministic provider. Its behavior is
exercised by the engine suites; the removal test derives affected test
files by import scan.
"""

from las.shared.module_contract import ModuleManifest

MODULE = ModuleManifest(
    id="providers.mock",
    version="1.0.0",
    parent="providers",
    summary="Deterministic scripted mock provider (tests, demo, self-test)",
    contract="las.modules.providers.mock",
    core=False,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=[],
    compat_shims=["las/providers/mock.py"],
)
