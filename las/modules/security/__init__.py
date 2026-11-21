"""Agency control: workspace jail, tool registry, permission broker.

Facade for the security module (spec 5.9, TLS-1..4). The workspace jail
lives in ``.workspace``; the broker, tool handlers and Rule-of-Two flags
in ``.tools``. The tool catalogue itself is a shared kernel contract.
"""

from las.shared.tool_catalog import (  # noqa: F401 - re-exported contract
    KNOWN_TOOLS, OFF_BY_DEFAULT_TOOLS, TOOL_DEFS, TOOL_FLAGS, WORKSPACE_TOOLS,
)
from .tools import BrokerDecision, PermissionBroker
from .workspace import Workspace, WorkspaceError

__all__ = [
    "KNOWN_TOOLS", "OFF_BY_DEFAULT_TOOLS", "TOOL_DEFS", "TOOL_FLAGS", "WORKSPACE_TOOLS",
    "BrokerDecision", "PermissionBroker", "Workspace", "WorkspaceError",
]
