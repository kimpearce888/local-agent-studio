"""Model lifecycle tests (PRV-8): list, load, unload.

llama.cpp router paths are spec-given (7.2); LM Studio's native paths are
an open item confirmed in Phase 0 (spec section 12), so its adapter
refuses lifecycle calls with that explanation instead of guessing.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.providers import ChatCompletionsAdapter, PRESETS
from las.modules.providers.mock import MockAdapter
from las.shared.provider_contract import ProviderError
from tests.test_adapters import MockServer


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")

    def tearDown(self):
        os.environ.pop("LAS_MOCK_SCRIPT", None)
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()


class TestMockLifecycle(CliTestBase):
    def test_mock_loaded_state_roundtrip(self):
        adapter = MockAdapter()
        self.assertTrue(adapter.list_models()[0]["loaded"])
        adapter.unload("mock-model")
        self.assertFalse(adapter.list_models()[0]["loaded"])
        adapter.preload("mock-model")
        self.assertTrue(adapter.list_models()[0]["loaded"])

    def test_cli_models_list_load_unload(self):
        code, out = self.run_cli("providers", "add", "--name", "m", "--preset", "mock")
        self.assertEqual(code, EXIT_OK, out)
        code, out = self.run_cli("models", "list", "--provider", "m")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("loaded", out)
        code, out = self.run_cli("models", "unload", "--provider", "m",
                                 "--model", "mock-model")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("Unloaded mock-model", out)
        code, out = self.run_cli("models", "load", "--provider", "m",
                                 "--model", "mock-model")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("Loaded mock-model", out)
        # load without a model is an actionable usage error
        code, out = self.run_cli("models", "load", "--provider", "m")
        self.assertEqual(code, EXIT_INVALID)


class TestWireLifecycle(unittest.TestCase):
    def test_llamacpp_router_load_and_unload(self):
        server = MockServer()   # serves from __init__
        self.addCleanup(server.close)
        adapter = ChatCompletionsAdapter(
            "llamacpp", server.base_url, preset=PRESETS["llamacpp"])
        adapter.preload("test-model")
        adapter.unload("test-model")
        # Two lifecycle calls recorded with the spec-given paths.
        self.assertEqual(len(server.recorded), 2)
        self.assertEqual(server.recorded[0], {"model": "test-model"})
        self.assertEqual(server.recorded[1], {"model": "test-model"})

    def test_lmstudio_native_paths_are_phase0_open_item(self):
        # The spec leaves LM Studio's native endpoint paths open (section
        # 12); the adapter must refuse rather than guess them.
        server = MockServer()   # serves from __init__
        self.addCleanup(server.close)
        adapter = ChatCompletionsAdapter(
            "lmstudio", server.base_url, preset=PRESETS["lmstudio"])
        with self.assertRaises(ProviderError) as ctx:
            adapter.preload("any")
        self.assertEqual(ctx.exception.code, "BAD_REQUEST")
        self.assertIn("Phase 0", str(ctx.exception))


class TestApiLifecycle(CliTestBase):
    def test_api_load_unload_endpoints(self):
        import threading
        from las.modules.api import serve
        from las.modules.storage import Store
        from las.shared.provider_contract import ProviderProfile
        Store(self.data_dir).save_provider(
            ProviderProfile(id="mock", name="mock", kind="mock"))
        server, token, _ = serve(self.data_dir)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        import urllib.request
        base = f"http://127.0.0.1:{server.server_address[1]}"
        def call(path):
            req = urllib.request.Request(
                base + path, headers={"X-Studio-Token": token}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        status, body = call("/providers/mock/models/mock-model/unload")
        self.assertEqual(status, 200)
        status, body = call("/providers/mock/models/mock-model/load")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()
