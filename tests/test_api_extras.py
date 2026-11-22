"""API-2 completion: OpenAPI description and local completion hooks."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from las.modules.api import serve
from las.modules.storage import Store
from las.shared.provider_contract import ProviderProfile
from tests.planlib import make_plan
from tests.util import success_turn


class ApiExtrasBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        Store(self.data_dir).save_provider(
            ProviderProfile(id="mock", name="mock", kind="mock"))
        self.server, self.token, _ = serve(self.data_dir)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        os.environ.pop("LAS_MOCK_SCRIPT", None)
        os.environ.pop("LAS_HOOK_OUT", None)
        self.tmp.cleanup()

    def call(self, path, method="GET", body=None, token=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Studio-Token": self.token if token is None else token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def start_run(self):
        from tests.util import success_turn
        plan = make_plan()
        turns = [success_turn(plan, t.id) for t in plan.tasks]
        script_path = os.path.join(self.tmp.name, "script.json")
        with open(script_path, "w", encoding="utf-8") as fh:
            json.dump(turns, fh)
        os.environ["LAS_MOCK_SCRIPT"] = script_path
        status, body = self.call("/runs", "POST", {"plan": plan.to_obj()})
        return body["run_id"]


class TestOpenApi(ApiExtrasBase):
    def test_openapi_served_without_token(self):
        status, doc = self.call("/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(doc["openapi"], "3.1.0")
        for path in ("/runs", "/runs/{id}", "/runs/{id}/events",
                     "/runs/{id}/tasks/{taskId}", "/providers",
                     "/providers/{id}/probe", "/plans", "/templates"):
            self.assertIn(path, doc["paths"])
        scheme = doc["components"]["securitySchemes"]["studioToken"]
        self.assertEqual(scheme["name"], "X-Studio-Token")
        # The document itself is exempt; everything else is not.
        self.assertEqual(doc["paths"]["/openapi.json"]["get"]["security"], [])

    def test_other_paths_still_need_token(self):
        req = urllib.request.Request(self.base + "/runs")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)


class TestCompletionHook(ApiExtrasBase):
    def test_hook_runs_with_run_context(self):
        hook_out = os.path.join(self.tmp.name, "hook-out.txt")
        os.environ["LAS_HOOK_OUT"] = hook_out
        Store(self.data_dir).set_setting(
            "runs.completion_hook",
            'echo "$LAS_RUN_ID $LAS_RUN_STATUS" > "$LAS_HOOK_OUT"')
        run_id = self.start_run()
        # 60s wall budget: throttled CI runners run slowly (see the
        # deflake note in test_api.py).
        for _ in range(int(60 / 0.05)):  # wait for the run + hook
            if os.path.exists(hook_out):
                break
            time.sleep(0.05)
        with open(hook_out, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), f"{run_id} succeeded")
        # The hook outcome is audited in the event log.
        events = Store(self.data_dir).events(run_id)
        hook_events = [e for e in events if e.type == "run.hook.executed"]
        self.assertEqual(len(hook_events), 1)
        self.assertEqual(hook_events[0].payload["exit_code"], 0)
        self.assertEqual(hook_events[0].payload["status"], "succeeded")

    def test_broken_hook_never_takes_the_engine_down(self):
        Store(self.data_dir).set_setting("runs.completion_hook",
                                         "exit 3")
        run_id = self.start_run()
        for _ in range(int(60 / 0.05)):
            if any(e.type == "run.hook.executed"
                   for e in Store(self.data_dir).events(run_id)):
                break
            time.sleep(0.05)
        ev = next(e for e in Store(self.data_dir).events(run_id)
                  if e.type == "run.hook.executed")
        self.assertEqual(ev.payload["exit_code"], 3)
        # The API still answers afterwards.
        status, body = self.call(f"/runs/{run_id}")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
