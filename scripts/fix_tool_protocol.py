#!/usr/bin/env python3
"""Surgical fix of the text tool protocol (transport-safe: no literal
wire-format tag sequences appear in this script).

Two changes to las/modules/context/__init__.py:

1. parse_text_tool_call gains a BARE-JSON branch between the historical
   wrapped form and the XML fallbacks.  The reference model, once the
   engine runs in text mode (probe-wired DriverConfig), emits the
   documented one-line JSON perfectly - but bare, without the wrapper,
   so it was never executed (measured: zero tool.called events in live
   runs).  Bare JSON is only accepted when the message is NOT a final
   handoff message, and the scan is brace-balanced (the wrapped form
   could rely on its closing marker as an anchor; bare JSON needs a
   real scanner to capture nested arguments).

2. TEXT_TOOL_PROTOCOL now teaches the bare one-line JSON form (what
   models actually comply with); the wrapped form remains accepted.
"""

import sys

PATH = "/home/z/my-project/local-agent-studio/las/modules/context/__init__.py"
src = open(PATH, encoding="utf-8").read()

# -- 1. the bare-JSON branch --------------------------------------------------
old = """        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    return _parse_xml_tool_call(text)"""
new = """        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    # Bare one-line JSON - what the prompt teaches and what real models
    # actually emit once text mode is wired (measured: perfect bare
    # {"name": ..., "arguments": {...}} lines, no wrapper at all).
    # Only when the message is not a final handoff message, and via a
    # balanced brace scan: the wrapped form had its closing marker as
    # an anchor, bare JSON needs a real scanner for nested arguments.
    if "<handoff" not in text.lower():
        for fragment in _balanced_json_fragments(text):
            try:
                obj = json.loads(fragment)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            name = str(obj.get("name", ""))
            args = obj.get("arguments", {})
            if name and isinstance(args, dict):
                return name, args
    return _parse_xml_tool_call(text)


def _balanced_json_fragments(text: str):
    \"\"\"Yield brace-balanced {...} fragments, outermost first at each
    opening position; the caller's json.loads decides validity.\"\"\"
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        j = i
        while j < n:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1
        i += 1"""
assert old in src, "bare-branch anchor not found"
src = src.replace(old, new, 1)

# -- 2. the protocol prompt ---------------------------------------------------
start = src.index('TEXT_TOOL_PROTOCOL = """')
end = src.index('"""', start + 25) + 3
new_prompt = '''TEXT_TOOL_PROTOCOL = """TEXT TOOL PROTOCOL (this model cannot call tools natively)
To call a tool, reply with EXACTLY ONE LINE of JSON in this format:
{"name": "list_files", "arguments": {}}
Example with an argument:
{"name": "search_files", "arguments": {"pattern": "report"}}
The tool result comes back in the next message. This JSON line is the
ONLY accepted tool-call format: do NOT use XML tags, fenced code
blocks or any other syntax - anything else is read as your final
message. When you have finished, send your final message ending with
the <handoff> block."""'''
src = src[:start] + new_prompt + src[end:]

open(PATH, "w", encoding="utf-8").write(src)
print("surgery applied; verifying:")

import las.modules.context as C  # noqa: E402
cases = [
    ('{"name": "list_files", "arguments": {}}', ("list_files", {})),
    ('{"name": "read_file", "arguments": {"path": "sales.csv"}}',
     ("read_file", {"path": "sales.csv"})),
    ("no tool call here", None),
    ("{broken json}", None),
]
ok = True
for text, want in cases:
    got = C.parse_text_tool_call(text)
    if got != want:
        ok = False
        print("  FAIL", repr(text), "->", got)
print("bare JSON parses:", ok)
print("protocol first line:",
      C.TEXT_TOOL_PROTOCOL.splitlines()[0][:60])
