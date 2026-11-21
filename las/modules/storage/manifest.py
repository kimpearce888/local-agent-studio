"""Manifest for the storage module (ADR-0002).

Dependencies describe implemented reality (actual imports post-S4).
"""

from las.shared.module_contract import CommandSpec, ModuleManifest

MODULE = ModuleManifest(
    id="storage",
    version="1.0.0",
    parent=None,
    summary="SQLite WAL store: hash-chained event log, providers/plans/templates/settings, run dirs",
    contract="las.modules.storage",
    core=True,
    requires=[],
    optional=[],
    default_enabled=True,
    commands=[],
    test_files=['tests/test_store_events.py'],
    compat_shims=[],
)
