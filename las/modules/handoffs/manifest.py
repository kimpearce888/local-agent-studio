"""Manifest for the handoffs module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="handoffs",
    version="1.0.0",
    parent=None,
    summary="Handoff schema, <handoff> extraction, H-rule validation",
    contract="las.modules.handoffs",
    core=True,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=['tests/test_handoff_validation.py'],
    compat_shims=[],
)
