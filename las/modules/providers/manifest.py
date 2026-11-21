"""Manifest for the providers module (ADR-0002).

Dependencies describe implemented reality: the providers layer consumes the
shared kernel port only (fix for finding F-01). The wire adapters are
tested in ``tests/test_adapters.py``.
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="providers",
    version="1.0.0",
    parent=None,
    summary="Provider connectivity: wire adapters (Chat Completions, Responses), "
            "presets, capability probe",
    contract="las.modules.providers",
    core=True,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[CommandSpec("providers", "Manage and test provider profiles"),
              CommandSpec("models", "Model lifecycle: list, load, unload (PRV-8)")],
    test_files=["tests/test_adapters.py", "tests/test_models.py"],
    compat_shims=[],
)
