"""las/workspace.py — moved to ``las.modules.security`` (ADR-0004).

Compatibility re-export at the pre-migration path; removed in migration step S7 (ADR-0004).
"""

from las.modules.security import Workspace, WorkspaceError  # noqa: F401

__all__ = ["Workspace", "WorkspaceError", ]
