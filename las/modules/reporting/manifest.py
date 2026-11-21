"""Manifest for the reporting module (ADR-0002).

Exports run reports (Markdown/JSON) with provenance and the AI notice
(OBS-4, COM-4). Depends on storage for run state and events; the storage
module moves into ``las/modules`` in S4, at which point this manifest's
``requires`` gains it (manifests describe implemented reality).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="reporting",
    version="1.0.0",
    parent=None,
    summary="Markdown and JSON export of runs with provenance (OBS-4, COM-4)",
    contract="las.modules.reporting",
    core=False,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[CommandSpec("export", "Export a run as a Markdown report or JSON bundle")],
    test_files=["tests/test_reporting.py"],
    compat_shims=["las/export.py"],
)
