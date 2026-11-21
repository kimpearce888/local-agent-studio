"""Manifest for the selftest module (ADR-0002).

The built-in three-task self-test with hard caps (DST-2 self-test step 5).
Optionally depends on ``providers.mock``: without the mock the self-test
reports itself unavailable instead of failing the run.
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="selftest",
    version="1.0.0",
    parent=None,
    summary="Built-in three-task self-test with hard caps (DST-2)",
    contract="las.modules.selftest",
    core=False,
    requires=["execution", "providers"],
    optional=["providers.mock"],
    default_enabled=True,
    commands=[CommandSpec("selftest", "Run the built-in self-test against a provider")],
    test_files=["tests/test_selftest.py"],
    compat_shims=[],
)
