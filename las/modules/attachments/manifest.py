"""Manifest for the attachments module (ADR-0002)."""

from las.shared.module_contract import ModuleManifest

MODULE = ModuleManifest(
    id="attachments",
    version="1.0.0",
    parent=None,
    summary="Goal attachments: import, planner manifest with summaries, workspace copy (PLN-7, TLS-8)",
    contract="las.modules.attachments",
    core=False,
    requires=["storage"],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=["tests/test_attachments.py"],
    compat_shims=[],
)
