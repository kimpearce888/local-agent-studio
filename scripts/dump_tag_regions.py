#!/usr/bin/env python3
"""Dump tag-marked source regions with '<' escaped, so transport cannot
strip them. Writes to /tmp and prints a pointer."""
import glob
import sys

sys.path.insert(0, "/home/z/my-project/local-agent-studio")

OUT = "/tmp/escaped_dump.txt"


def esc(s: str) -> str:
    return s.replace("<", "\x01")


with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("=== context/__init__.py TEXT_TOOL_PROTOCOL region ===\n")
    src = open("/home/z/my-project/local-agent-studio/las/modules/context/__init__.py",
               encoding="utf-8").read()
    start = src.index("TEXT_TOOL_PROTOCOL")
    end = src.index('"""', start + 20) + 3
    fh.write(esc(src[start:end + 40]))
    fh.write("\n\n=== TOOL_CALL_RE line ===\n")
    for line in src.splitlines():
        if "TOOL_CALL_RE = " in line:
            fh.write(esc(line) + "\n")
    fh.write("\n=== tests mentioning the protocol / tool calls ===\n")
    for path in glob.glob("/home/z/my-project/local-agent-studio/tests/*.py"):
        t = open(path, encoding="utf-8").read()
        if "tool_call" in t or "TEXT_TOOL" in t or "TOOL_CALL" in t:
            fh.write(f"--- {path} ---\n")
            for i, line in enumerate(t.splitlines(), 1):
                if ("tool_call" in line or "TEXT_TOOL" in line
                        or "protocol" in line.lower()
                        or "```" in line):
                    fh.write(esc(f"{i}: {line}") + "\n")
print("wrote", OUT)
