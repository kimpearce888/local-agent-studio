"""Manifest for the security module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="security",
    version="1.0.0",
    parent=None,
    summary="Agency control: workspace jail, tool registry, permission broker, Rule of Two",
    contract="las.modules.security",
    core=True,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=['tests/test_workspace.py', 'tests/test_tools_broker.py'],
    compat_shims=[],
)
