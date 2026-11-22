#!/usr/bin/env python3
"""Diagnose the TOOL_CALL_RE mismatch: runtime module vs source on disk."""
import inspect
import sys

sys.path.insert(0, "/home/z/my-project/local-agent-studio")
import las.modules.context as C  # noqa: E402

print("module file:", C.__file__)
pat = C.TOOL_CALL_RE.pattern
print("runtime pattern len:", len(pat))
print("runtime pattern repr:", repr(pat))
print("search plain braces:", C.TOOL_CALL_RE.search("{a}"))
print("--- parse_text_tool_call source (runtime) ---")
try:
    print(inspect.getsource(C.parse_text_tool_call)[:500])
except Exception as exc:  # noqa: BLE001
    print("getsource failed:", exc)
print("--- source file line with TOOL_CALL_RE (disk) ---")
for line in open(C.__file__, encoding="utf-8"):
    if "TOOL_CALL_RE = " in line:
        print("DISK:", line.rstrip())
