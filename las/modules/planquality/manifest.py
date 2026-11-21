"""Manifest for the planquality module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="planquality",
    version="1.0.0",
    parent=None,
    summary="Plan validation (V rules) and linting (L rules)",
    contract="las.modules.planquality",
    core=True,
    requires=['context', 'handoffs', 'security'],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=['tests/test_plan_validation.py', 'tests/test_linter.py'],
    compat_shims=['las/linter.py', 'las/schema/plan.py'],
)
