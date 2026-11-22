"""API module tests (spec 7.5): token auth, origin check, providers, plans,
runs end to end against the mock provider, events, export."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from las.modules.api import serve  # noqa: E402
from las.modules.storage import Store  # noqa: E402
from las.shared.provider_contract import ProviderProfile  # noqa: E402


def module_installed(module_id: str) -> bool:
    try:
        from las.app.registry import ModuleRegistry
        ModuleRegistry().get(module_id)
        return True
    except Exception:  # noqa: BLE001 - ModuleNotInstalledError
        return False


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="las-api-")
        Store(self.data_dir).save_provider(
            ProviderProfile(id="mock", name="mock", kind="mock"))
        self.server, self.token, self.token_path = serve(self.data_dir)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, headers=None, method="GET", body=None):
        h = {"X-Studio-Token": self.token}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, headers=h, method=method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())


class TestSecurity(ApiTestBase):
    def test_health_requires_token(self):
        status, body = self.call("/health", headers={"X-Studio-Token": "wrong"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "AUTH")

    def test_origin_check(self):
        status, body = self.call("/health", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_health_ok(self):
        status, body = self.call("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_token_file_mode_0600(self):
        self.assertEqual(os.stat(self.token_path).st_mode & 0o777, 0o600)


class TestProviders(ApiTestBase):
    def test_provider_crud_roundtrip(self):
        status, body = self.call("/providers", method="POST",
                                 body={"id": "lmstudio", "kind": "lmstudio"})
        self.assertEqual(status, 200)
        status, body = self.call("/providers")
        self.assertTrue(any(p["id"] == "lmstudio" for p in body["providers"]))
        status, body = self.call("/providers/lmstudio", method="PUT",
                                 body={"base_url": "http://localhost:1234"})
        self.assertEqual(body["base_url"], "http://localhost:1234")
        status, _ = self.call("/providers/lmstudio", method="DELETE")
        self.assertEqual(status, 200)

    def test_unknown_provider_404(self):
        status, body = self.call("/providers/ghost")
        self.assertEqual(status, 404)


class TestRuns(ApiTestBase):
    def _plan(self):
        return {
            "schema_version": 1, "id": "p1", "title": "T", "created_at": "now",
            "goal": "Do it.", "options": {},
            "constraints": [{"id": "c1", "text": "You must cite every figure."}],
            "tasks": [
                {"id": "t1", "title": "One", "instructions": "Gather facts and write facts.md.",
                 "needs": [], "constraints": ["c1"]},
                {"id": "t2", "title": "Two", "type": "collector",
                 "instructions": "Combine everything into the final brief.",
                 "needs": ["t1"], "constraints": ["c1"]},
            ],
        }

    def _script(self):
        def handoff(tid):
            return ("Done.\n<handoff>\n" + json.dumps({
                "task_id": tid, "status": "done", "summary": f"Finished {tid}.",
                "key_facts": [], "binding_constraints": [
                    {"id": "c1", "text": "You must cite every figure.", "state": "active"}],
                "decisions": [], "open_issues": [], "deliverables": [],
            }, indent=1) + "\n</handoff>")
        script = [{"text": handoff("t1")}, {"text": handoff("t2")}]
        path = os.path.join(self.data_dir, "script.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(script, fh)
        os.environ["LAS_MOCK_SCRIPT"] = path

    @unittest.skipUnless(module_installed("providers.mock"),
                         "fixture uses the providers.mock module")
    def test_run_end_to_end_events(self):
        self._script()
        try:
            status, body = self.call("/runs", method="POST", body={"plan": self._plan()})
            self.assertEqual(status, 200, body)
            run_id = body["run_id"]
            import time
            # 60s wall budget: a throttled CI runner can boot the
            # engine ~10x slower than a laptop (CI run 28 flaked
            # when this deadline was 10s).
            for _ in range(int(60 / 0.05)):
                status, state = self.call(f"/runs/{run_id}")
                if state.get("status") in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(0.05)
            self.assertEqual(state["status"], "succeeded", state)
            status, body = self.call(f"/runs/{run_id}/events")
            self.assertGreater(len(body["events"]), 5)
            types = [e["type"] for e in body["events"]]
            self.assertIn("run.created", types)
            self.assertIn("run.succeeded", types)
            status, body = self.call(f"/runs/{run_id}/export?format=markdown")
            if module_installed("reporting"):
                self.assertEqual(status, 200)
                self.assertIn(run_id, body["content"])
            else:
                # Designed degradation: the export endpoint reports that the
                # optional reporting module is unavailable.
                self.assertEqual(status, 409)
        finally:
            os.environ.pop("LAS_MOCK_SCRIPT", None)

    @unittest.skipUnless(module_installed("providers.mock"),
                         "fixture uses the providers.mock module")
    def test_events_after_seq_polling(self):
        self._script()
        try:
            _, body = self.call("/runs", method="POST", body={"plan": self._plan()})
            run_id = body["run_id"]
            import time
            time.sleep(0.3)
            for _ in range(int(60 / 0.05)):
                _, st = self.call(f"/runs/{run_id}")
                if st.get("status") in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(0.05)
            _, first = self.call(f"/runs/{run_id}/events")
            n = len(first["events"])
            _, rest = self.call(f"/runs/{run_id}/events?after={n}")
            self.assertEqual(rest["events"], [])
        finally:
            os.environ.pop("LAS_MOCK_SCRIPT", None)

    def test_unknown_route_404(self):
        status, body = self.call("/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
