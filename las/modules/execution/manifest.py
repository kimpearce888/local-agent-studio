"""Manifest for the execution module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="execution",
    version="1.0.0",
    parent=None,
    summary="Sequential run driver: gates, failure table, budgets, controls, replay, rerun",
    contract="las.modules.execution",
    core=True,
    requires=['context', 'handoffs', 'providers', 'security', 'storage'],
    optional=[],
    default_enabled=True,
    commands=[
        CommandSpec("run", "Run a plan or template"),
        CommandSpec("list", "List runs"),
        CommandSpec("inspect", "Show a run"),
        CommandSpec("resume", "Resume a paused or failed run"),
        CommandSpec("approve", "Approve a gate"),
        CommandSpec("reject", "Reject a gate"),
        CommandSpec("retry", "Retry a failed task"),
        CommandSpec("skip", "Skip a task (dependents are skipped too)"),
        CommandSpec("rerun", "Rerun from a task (later tasks go stale)"),
        CommandSpec("verify", "Verify a run's event hash chain"),
    ],
    test_files=['tests/test_scheduler.py'],
    compat_shims=['las/scheduler.py', 'las/runstate.py'],
)
