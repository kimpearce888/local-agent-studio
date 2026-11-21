"""Manifest for the planning module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="planning",
    version="1.0.0",
    parent=None,
    summary="Goal-to-plan: planner contract, repair loop, critique pass, constraint extraction",
    contract="las.modules.planning",
    core=True,
    requires=['handoffs', 'planquality'],
    optional=[],
    default_enabled=True,
    commands=[
        CommandSpec("plan", "Generate a plan from a goal"),
    ],
    test_files=['tests/test_planner.py'],
    compat_shims=[],
)
