"""Manifest for the context module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="context",
    version="1.0.0",
    parent=None,
    summary="Context assembly (blocks 1-7), token budget, compaction ladder",
    contract="las.modules.context",
    core=True,
    requires=['handoffs', 'security'],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=['tests/test_context.py'],
    compat_shims=['las/context.py'],
)
