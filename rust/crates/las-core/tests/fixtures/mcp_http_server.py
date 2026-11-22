#!/usr/bin/env python3
"""Fake MCP Streamable HTTP server for the las-core client tests.

Speaks the MCP Streamable HTTP transport: JSON-RPC 2.0 request
bodies POSTed as application/json, responses as application/json
(plain mode) or text/event-stream (LAS_HTTP_MODE=sse).  Enforces
the session discipline: initialize answers with an Mcp-Session-Id
header, and every later request without that header is refused
with HTTP 400 - so the tests prove the client really carries the
session.  Notifications (no id) get 202 Accepted.

Endpoint: any path (tests POST to /mcp).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SESSION = "sess-fixture-1234"
SSE = os.environ.get("LAS_HTTP_MODE", "") == "sse"
PORT = int(sys.argv[1])

TOOLS = [
    {"name": "echo",
     "description": "echoes text back with a bang",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}}}},
    {"name": "boom",
     "description": "always reports an error",
     "inputSchema": {"type": "object"}},
]


def result(mid, payload):
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def handle(msg):
    """Return (status, body_value_or_None) for one JSON-RPC message."""
    if "id" not in msg:
        return 202, None   # notification: accepted, no response
    mid = msg["id"]
    method = msg.get("method", "")
    if method == "initialize":
        return 200, result(mid, {
            "protocolVersion":
                msg.get("params", {}).get("protocolVersion",
                                          "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "http-fixture",
                           "version": "1.0"}})
    if method == "tools/list":
        return 200, result(mid, {"tools": TOOLS})
    if method == "resources/list":
        return 200, result(mid, {"resources": []})
    if method == "prompts/list":
        return 200, result(mid, {"prompts": []})
    if method == "tools/call":
        name = msg.get("params", {}).get("name", "")
        args = msg.get("params", {}).get("arguments") or {}
        if name == "echo":
            return 200, result(mid, {
                "content": [{"type": "text",
                             "text": str(args.get("text", "")) + "!"}],
                "isError": False})
        if name == "boom":
            return 200, result(mid, {
                "content": [{"type": "text", "text": "boom"}],
                "isError": True})
        return 200, {"jsonrpc": "2.0", "id": mid,
                     "error": {"code": -32602,
                               "message": f"unknown tool {name}"}}
    return 200, {"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32601,
                           "message": "method not found"}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            msg = json.loads(body)
        except ValueError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        is_init = msg.get("method") == "initialize"
        if not is_init and \
                self.headers.get("Mcp-Session-Id") != SESSION:
            # Session discipline: post-initialize requests MUST
            # carry the session the server assigned.
            out = b"{}"
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        status, value = handle(msg)
        if value is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = json.dumps(value)
        if SSE:
            # Streamable HTTP SSE shape: the response as an event.
            out = (f"event: message\n"
                   f"data: {payload}\n\n").encode()
            ctype = "text/event-stream"
        else:
            out = payload.encode()
            ctype = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if is_init:
            self.send_header("Mcp-Session-Id", SESSION)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_args):
        pass   # keep the test output clean


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
