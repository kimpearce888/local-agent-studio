"""Tool catalogue (spec 5.9) — shared kernel contract.

Declarative data: which tools exist, their definitions and their
Rule-of-Two flags. Used by planning (prompts), planquality (V-11, L-01)
and security (the broker enforces). The broker itself lives in the
security module; only the catalogue is shared.
"""

from __future__ import annotations

from las.shared.provider_contract import ToolDef

WORKSPACE_TOOLS = ("list_files", "read_file", "write_file", "append_file",
                   "search_files", "patch_file")
OFF_BY_DEFAULT_TOOLS = ("run_command", "web_fetch")
KNOWN_TOOLS = set(WORKSPACE_TOOLS) | set(OFF_BY_DEFAULT_TOOLS)

TOOL_FLAGS: dict[str, tuple[bool, bool, bool]] = {
    #   A(untrusted input) B(sensitive access) C(external effect)
    "list_files": (False, False, False),
    "read_file": (False, False, False),
    "write_file": (False, False, False),
    "append_file": (False, False, False),
    "search_files": (False, False, False),
    "patch_file": (False, False, False),
    "run_command": (False, True, True),
    "web_fetch": (True, False, True),
}

TOOL_DEFS: dict[str, ToolDef] = {
    "list_files": ToolDef(
        "list_files", "List files in the run workspace, optionally under a prefix.",
        {"type": "object", "properties": {"prefix": {"type": "string"}}, "required": []}),
    "read_file": ToolDef(
        "read_file", "Read a text file from the run workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}},
         "required": ["path"]}),
    "write_file": ToolDef(
        "write_file", "Write a text file in the run workspace (overwrites).",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}),
    "append_file": ToolDef(
        "append_file", "Append text to a file in the run workspace.",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}),
    "search_files": ToolDef(
        "search_files", "Search file contents with a regular expression.",
        {"type": "object",
         "properties": {"pattern": {"type": "string"}, "prefix": {"type": "string"}},
         "required": ["pattern"]}),
    "patch_file": ToolDef(
        "patch_file", "Replace text inside a file in the run workspace.",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "find": {"type": "string"},
                        "replace": {"type": "string"}, "all": {"type": "boolean"}},
         "required": ["path", "find", "replace"]}),
    "run_command": ToolDef(
        "run_command",
        "Run an allowlisted command in the run workspace (no network, short timeout).",
        {"type": "object",
         "properties": {"command": {"type": "array", "items": {"type": "string"}},
                        "timeout_seconds": {"type": "number"}},
         "required": ["command"]}),
    "web_fetch": ToolDef(
        "web_fetch", "Fetch one page from an allowlisted domain. Results are untrusted.",
        {"type": "object", "properties": {"url": {"type": "string"}},
         "required": ["url"]}),
}
