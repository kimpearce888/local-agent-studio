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
    "handoff_repair_rounds": 1,
    "cap_tokens_run": 2_000_000,
    "cap_wall_minutes_run": 480,
    "context_length": 8192,
}
BACKOFF_SCHEDULE = (2.0, 8.0, 30.0)   # spec 5.7, server/network errors

# Handoff repair budgeting (spec 5.5): the configured rounds plus one on a
# long chain, capped at the planner's three-round ceiling (PLN-2).  The
# long-chain bonus is measured, not guessed: the two v0.4.8 rerun failures
# were late-stage HANDOFF-INVALIDs on 8- and 10-task chains where a single
# repair round was not enough (short chains never needed a second).
LONG_CHAIN_TASKS = 6
MAX_HANDOFF_REPAIR_ROUNDS = 3

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
    # Per-task attempt wall (spec 5.8), configurable per run: real
    # multi-turn file work through a buffered non-streaming backend
    # (the z-ai proxy: one HTTP request covers the model's ENTIRE
    # generation, measured 300-600s per turn) needs far more than the
    # mock-speed default.
    wall_task_seconds: int = DEFAULTS["wall_task_seconds"]
    # Handoff repair rounds per task attempt (spec 5.5): the base budget,
    # raised by one on a chain of LONG_CHAIN_TASKS or more tasks (see the
    # measured note above), never above MAX_HANDOFF_REPAIR_ROUNDS.
    handoff_repair_rounds: int = DEFAULTS["handoff_repair_rounds"]
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

    def to_public_obj(self) -> dict:
        """The frozen-execution subset recorded in run.created (audit
        item 18: resume restores the original configuration)."""
        return {
            "failure_policy": self.failure_policy,
            "cap_tokens_run": self.cap_tokens_run,
            "cap_wall_minutes_run": self.cap_wall_minutes_run,
            "stall_timeout": self.stall_timeout,
            "context_length": self.context_length,
            "wall_task_seconds": self.wall_task_seconds,
            "handoff_repair_rounds": self.handoff_repair_rounds,
            "enable_shell": self.enable_shell,
            "enable_web": self.enable_web,
            "command_allowlist": list(self.command_allowlist),
            "web_domains": list(self.web_domains),
        }

    @classmethod
    def from_public_obj(cls, obj: dict) -> "DriverConfig":
        known = {k: v for k, v in (obj or {}).items()
                 if k in cls.__dataclass_fields__}
        return cls(**known)
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
