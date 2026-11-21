"""Budgets, defaults and driver data types (spec 5.7, 5.8).

Private implementation file of the execution module. There is no unlimited
setting for any cap (RUN-8).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# Default budgets (spec 5.8).  There is no unlimited setting.
DEFAULTS = {
    "max_output_tokens": 4096,
    "wall_task_seconds": 1800,
    "stall_timeout": 120.0,
    "max_turns": 25,
    "max_tool_calls": 100,
    "retries": 2,
    "cap_tokens_run": 2_000_000,
    "cap_wall_minutes_run": 480,
    "context_length": 8192,
}
BACKOFF_SCHEDULE = (2.0, 8.0, 30.0)   # spec 5.7, server/network errors

NO_RETRY_CODES = {"RUN-CONTEXT-OVERFLOW", "MODEL-UNAVAILABLE", "CAP-REACHED",
                  "PERMISSION-DENIED", "TOOL-CALL-INVALID", "HANDOFF-INVALID"}


class StallError(Exception):
    pass


class PauseRequested(Exception):
    pass


class CancelRequested(Exception):
    pass


@dataclass
class StepResult:
    kind: str                      # task_succeeded | task_failed | task_skipped |
                                   # gate_waiting | run_succeeded | run_failed |
                                   # paused | cancelled
    task_id: str = ""
    code: str = ""
    detail: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class DriverConfig:
    failure_policy: str = "ask"            # ask | stop | skip
    cap_tokens_run: int = DEFAULTS["cap_tokens_run"]
    cap_wall_minutes_run: int = DEFAULTS["cap_wall_minutes_run"]
    stall_timeout: float = DEFAULTS["stall_timeout"]
    context_length: int = DEFAULTS["context_length"]
    tool_calling: bool = True              # capability: native tool calls
    enable_shell: bool = False
    enable_web: bool = False
    command_allowlist: list = field(default_factory=list)
    web_domains: list = field(default_factory=list)
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    # Injection seams (finding F-10): replacement tests and alternative
    # security setups supply these instead of the defaults constructed by
    # the driver.
    workspace_factory: Optional[Callable] = None   # (store, run_id) -> Workspace
    broker_factory: Optional[Callable] = None      # (workspace, config) -> PermissionBroker


@dataclass
class AttemptOutcome:
    ok: bool = False
    code: str = ""
    kind: str = ""                      # ok | provider_error | stall | loop | terminal | control
    attempt_id: str = ""
    handoff: Optional["object"] = None  # Handoff (typed in the facade)
    final_text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
