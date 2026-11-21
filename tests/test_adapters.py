"""Wire adapters against a local SSE mock server (spec 7.1, 7.2, AC-09).

Exercises the real HTTP path: SSE parsing, tool-call delta accumulation,
usage extraction, error normalization and the Unsloth ``enable_tools:
false`` policy on every request (PRV-5).
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from las.shared.provider_contract import ChatRequest, Message, ProviderError, ToolDef
from las.modules.providers import (
    ChatCompletionsAdapter, ResponsesAdapter, resolve_api_key,
)


class MockServer:
    """A scripted OpenAI-compatible server on an ephemeral port."""

    def __init__(self):
        self.responses: list[dict] = []      # queued SSE payloads per request
        self.recorded: list[dict] = []       # request bodies
        self.force_status: int | None = None
        self.sleep_seconds: float = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # silence
                pass

            def _reply(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if outer.force_status:
                    self._reply(outer.force_status, b"{}")
                    return
                if self.path == "/v1/models":
                    body = json.dumps({"data": [
                        {"id": "test-model", "max_context_length": 8192},
                        {"id": "other-model"}]}).encode()
                    self._reply(200, body)
                else:
                    self._reply(404, b'{"error": {"message": "not found"}}')

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    outer.recorded.append(json.loads(raw))
                except json.JSONDecodeError:
                    outer.recorded.append({})
                if self.path in ("/models/load", "/models/unload"):
                    self._reply(200, b'{"ok": true}')
                    return
                if outer.force_status:
                    self._reply(outer.force_status, b'{"error": {"message": "nope"}}')
                    return
                if outer.sleep_seconds:
                    import time
                    time.sleep(outer.sleep_seconds)
                if self.path == "/v1/chat/count_tokens":
                    self._reply(200, json.dumps({"token_count": 42}).encode())
                    return
                script = outer.responses.pop(0) if outer.responses else {
                    "text": "", "tool_calls": [], "usage": [0, 0]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in _sse_chunks(script):
                    self.wfile.write(chunk)
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _sse_chunks(script: dict) -> list[bytes]:
    chunks: list[bytes] = []
    text = script.get("text", "")
    step = max(1, len(text) // 2)
    for i in range(0, len(text), step):
        piece = text[i:i + step]
        chunks.append(b"data: " + json.dumps(
            {"choices": [{"delta": {"content": piece}}]}).encode() + b"\n\n")
    for i, tc in enumerate(script.get("tool_calls", [])):
        args = tc.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args)
        half = max(1, len(args) // 2)
        chunks.append(b"data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": i, "id": tc.get("id", f"call_{i}"),
             "function": {"name": tc["name"], "arguments": args[:half]}}]}}]}).encode()
            + b"\n\n")
        chunks.append(b"data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": i,
             "function": {"arguments": args[half:]}}]}}]}).encode() + b"\n\n")
    uin, uout = script.get("usage", [0, 0])
    chunks.append(b"data: " + json.dumps({
        "choices": [{"delta": {}}],
        "usage": {"prompt_tokens": uin, "completion_tokens": uout}}).encode() + b"\n\n")
    chunks.append(b"data: [DONE]\n\n")
    return chunks


class AdapterTestBase(unittest.TestCase):
    def setUp(self):
        self.server = MockServer()

    def tearDown(self):
        self.server.close()

    def request(self) -> ChatRequest:
        return ChatRequest(
            model="test-model",
            messages=[Message("system", "Be brief."),
                      Message("user", "Say hello.")],
            tools=[ToolDef("ping", "Ping.", {"type": "object"})])


class TestChatCompletionsAdapter(AdapterTestBase):
    def adapter(self, preset="generic", api_key=None):
        return ChatCompletionsAdapter("test", self.server.base_url,
                                      api_key=api_key, preset=preset, timeout=5)

    def collect(self, adapter, request):
        text = ""
        tool_calls = []
        usage = None
        for ev in adapter.chat(request):
            if ev.kind == "text_delta":
                text += ev.text
            elif ev.kind == "tool_call":
                tool_calls.append(ev.tool_call)
            elif ev.kind == "usage":
                usage = ev.usage
        return text, tool_calls, usage

    def test_streaming_text_and_usage(self):
        self.server.responses = [{"text": "Hello there", "usage": [11, 7]}]
        text, calls, usage = self.collect(self.adapter(), self.request())
        self.assertEqual(text, "Hello there")
        self.assertEqual(calls, [])
        self.assertEqual((usage.tokens_in, usage.tokens_out), (11, 7))

    def test_tool_call_deltas_accumulate(self):
        self.server.responses = [{
            "text": "", "usage": [5, 3],
            "tool_calls": [{"name": "write_file",
                            "arguments": {"path": "out.md", "content": "hi"}}]}]
        text, calls, usage = self.collect(self.adapter(), self.request())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "write_file")
        self.assertEqual(json.loads(calls[0].arguments),
                         {"path": "out.md", "content": "hi"})

    def test_full_history_sent_every_time(self):
        self.server.responses = [{"text": "ok", "usage": [1, 1]}]
        adapter = self.adapter()
        request = self.request()
        self.collect(adapter, request)
        body = self.server.recorded[0]
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_json_schema_request(self):
        self.server.responses = [{"text": "{}", "usage": [1, 1]}]
        request = self.request()
        request.json_schema = {"type": "object"}
        self.collect(self.adapter(), request)
        body = self.server.recorded[0]
        self.assertEqual(body["response_format"]["type"], "json_schema")

    def test_list_models(self):
        models = self.adapter().list_models()
        self.assertEqual([m["id"] for m in models], ["test-model", "other-model"])
        self.assertEqual(models[0]["context_length"], 8192)

    def test_ac09_unsloth_always_disables_provider_tools(self):
        self.server.responses = [
            {"text": "a", "usage": [1, 1]}, {"text": "b", "usage": [1, 1]}]
        adapter = self.adapter(preset="unsloth")
        self.collect(adapter, self.request())
        self.collect(adapter, self.request())
        self.assertEqual(len(self.server.recorded), 2)
        for body in self.server.recorded:
            self.assertIs(body.get("enable_tools"), False)   # AC-09
            self.assertTrue(body.get("stream"))

    def test_generic_preset_does_not_send_enable_tools(self):
        self.server.responses = [{"text": "a", "usage": [1, 1]}]
        self.collect(self.adapter(), self.request())
        self.assertNotIn("enable_tools", self.server.recorded[0])

    def test_count_tokens_on_unsloth_preset(self):
        adapter = self.adapter(preset="unsloth")
        self.assertEqual(adapter.count_tokens([Message("user", "hi")], "m"), 42)

    def test_auth_error_normalized(self):
        self.server.force_status = 401
        with self.assertRaises(ProviderError) as ctx:
            self.adapter().list_models()
        self.assertEqual(ctx.exception.code, "AUTH")

    def test_server_error_normalized(self):
        self.server.force_status = 500
        with self.assertRaises(ProviderError) as ctx:
            self.adapter().list_models()
        self.assertEqual(ctx.exception.code, "SERVER_ERROR")

    def test_timeout_normalized(self):
        self.server.sleep_seconds = 1.5
        adapter = ChatCompletionsAdapter("test", self.server.base_url,
                                         preset="generic", timeout=0.2)
        with self.assertRaises(ProviderError) as ctx:
            self.collect(adapter, self.request())
        self.assertEqual(ctx.exception.code, "TIMEOUT")
        self.server.sleep_seconds = 0


class TestResponsesAdapter(AdapterTestBase):
    def test_streaming_events_mapped(self):
        events = [
            {"type": "response.output_text.delta", "delta": "Hi "},
            {"type": "response.output_text.delta", "delta": "there"},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "call_id": "c1",
                      "name": "read_file", "arguments": '{"path": "a.md"}'}},
            {"type": "response.completed",
             "response": {"usage": {"input_tokens": 9, "output_tokens": 4}}},
        ]

        outer = self

        class FixedHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.server.recorded.append(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for ev in events:
                    self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        # Swap the handler for this one request.
        original = self.server.server.RequestHandlerClass
        self.server.server.RequestHandlerClass = FixedHandler
        try:
            adapter = ResponsesAdapter("test", self.server.base_url, timeout=5)
            text = ""
            calls = []
            usage = None
            for ev in adapter.chat(self.request()):
                if ev.kind == "text_delta":
                    text += ev.text
                elif ev.kind == "tool_call":
                    calls.append(ev.tool_call)
                elif ev.kind == "usage":
                    usage = ev.usage
            self.assertEqual(text, "Hi there")
            self.assertEqual(calls[0].name, "read_file")
            self.assertEqual(json.loads(calls[0].arguments), {"path": "a.md"})
            self.assertEqual((usage.tokens_in, usage.tokens_out), (9, 4))
            # Full history went into input; no previous_response_id (D3).
            body = self.server.recorded[-1]
            self.assertIn("input", body)
            self.assertNotIn("previous_response_id", body)
        finally:
            self.server.server.RequestHandlerClass = original


class TestResolveApiKey(unittest.TestCase):
    def test_env_reference(self):
        import os
        os.environ["LAS_TEST_KEY"] = "sk-test-123"
        try:
            self.assertEqual(resolve_api_key("env:LAS_TEST_KEY"), "sk-test-123")
        finally:
            del os.environ["LAS_TEST_KEY"]
        self.assertIsNone(resolve_api_key("env:LAS_MISSING_KEY_XYZ"))

    def test_plain_value_passthrough(self):
        self.assertEqual(resolve_api_key("literal"), "literal")
        self.assertIsNone(resolve_api_key(""))


if __name__ == "__main__":
    unittest.main()
