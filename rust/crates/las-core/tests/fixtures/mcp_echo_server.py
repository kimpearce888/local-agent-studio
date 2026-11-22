#!/usr/bin/env python3
"""Fake MCP stdio server for the las-core client tests.

Speaks newline-delimited JSON-RPC 2.0 (the MCP stdio transport):
- initialize -> echoes the offered protocolVersion, reports itself
  as `echo-fixture`.
- tools/list -> two tools: `echo` (replies "text" + "!") and `boom`
  (always isError).
- resources/list, prompts/list -> empty results.
- LAS_FAKE_DIE_AFTER=N env: exits after N requests are ANSWERED, to
  exercise the client's crash reporting and respawn path.
"""
import json
import os
import sys

DIE_AFTER = int(os.environ.get("LAS_FAKE_DIE_AFTER", "0"))
answered = 0


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue   # notifications need no reply
    method = msg.get("method", "")
    mid = msg["id"]
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion":
                msg.get("params", {}).get("protocolVersion",
                                          "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo-fixture",
                           "version": "1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo",
             "description": "echoes text back with a bang",
             "inputSchema": {
                 "type": "object",
                 "properties": {"text": {"type": "string"}},
                 "required": ["text"]}},
            {"name": "boom",
             "description": "always reports an error",
             "inputSchema": {"type": "object"}},
        ]}})
    elif method == "tools/call":
        name = msg.get("params", {}).get("name", "")
        args = msg.get("params", {}).get("arguments") or {}
        if name == "echo":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": str(args.get("text", "")) + "!"}],
                "isError": False}})
        elif name == "boom":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "boom"}],
                "isError": True}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32602, "message": f"unknown tool {name}"}})
    elif method == "resources/list":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"resources": []}})
    elif method == "prompts/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {
            "code": -32601, "message": "method not found"}})
    answered += 1
    if DIE_AFTER and answered >= DIE_AFTER:
        sys.exit(0)
