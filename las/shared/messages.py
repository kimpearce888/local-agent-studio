"""Message catalogue (spec section 8).

Every error has a plain message and a fix. Messages are at most two
sentences, name the thing that failed, and never blame the user.
"""

from __future__ import annotations

# code -> (message template, fix)
MESSAGES: dict[str, tuple[str, str]] = {
    "AUTH": (
        "The server rejected the key.",
        "Create a new key in the server's settings and replace it under Providers.",
    ),
    "UNREACHABLE": (
        "Cannot reach the server at {url}.",
        "Start the server and its local API, then press Test connection.",
    ),
    "MODEL_NOT_FOUND": (
        "The model {id} is not available on this server.",
        "Load it in the server, or choose another model.",
    ),
    "RUN-CONTEXT-OVERFLOW": (
        "This task's input is larger than the model's context.",
        "Split the task, or choose a model with a longer context.",
    ),
    "HANDOFF-INVALID": (
        "The result did not follow the required format: {rules}.",
        "Retry, edit the task instructions, or use a stronger model.",
    ),
    "TOOL-CALL-INVALID": (
        "The model made a tool call that could not be read.",
        "Retry, or turn tools off for this task.",
    ),
    "LOOP-DETECTED": (
        "The agent repeated itself and was stopped.",
        "Retry with clearer instructions or another model.",
    ),
    "CAP-REACHED": (
        "The run reached its {cap} cap.",
        "Raise the cap in Settings > Runs, then resume.",
    ),
    "PERMISSION-DENIED": (
        "The task tried to use {tool}, which it is not allowed to use.",
        "Grant the tool in the task's Tools tab if you trust this task.",
    ),
    "PLAN-INVALID": (
        "The plan has {n} problems.",
        "Open the Lint tab and fix the errors.",
    ),
    "GATE-REQUIRED": (
        "This task needs your approval before it runs.",
        "Review it and approve it in the run view.",
    ),
    "DB-NEWER": (
        "This data was written by a newer version of the app.",
        "Update the app. Your data was not changed.",
    ),
    "UNSIGNED-UPDATE": (
        "The update could not be verified and was not installed.",
        "Download the installer again from the official page.",
    ),
    "DOWNGRADE-BLOCKED": (
        "The update is older than the installed version and was refused.",
        "No action is needed.",
    ),
    "STALE-STATE": (
        "The run state file is inconsistent with the event log.",
        "The engine rebuilt the state from the event log.",
    ),
}


def message(code: str, **params) -> str:
    """Return the human message for a code, with placeholders filled."""
    msg, _fix = MESSAGES.get(code, ("{code} failed.", "See the documentation."))
    try:
        return msg.format(code=code, **params)
    except KeyError:
        return msg


def message_with_fix(code: str, **params) -> tuple[str, str]:
    """Return (message, fix) for a code, with placeholders filled."""
    msg, fix = MESSAGES.get(code, ("{code} failed.".format(code=code), "See the documentation."))
    try:
        return msg.format(code=code, **params), fix.format(code=code, **params)
    except KeyError:
        return msg, fix
