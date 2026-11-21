"""OBS-5: optional OTLP export - off by default, local-only, pinned
GenAI conventions, never fails a run."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from las.modules.storage import Store
from las.modules.telemetry import PINNED_CONVENTION, is_local, maybe_export
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class Receiver:
    """A local OTLP receiver capturing POSTs to /v1/traces."""

    def __init__(self):
        outer = self
        self.bodies = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.bodies.append(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TelemetryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self.store = Store(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def finished_run(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        return run_id, plan


class TestOffByDefault(TelemetryBase):
    def test_no_endpoint_means_no_export(self):
        run_id, plan = self.finished_run()
        self.assertIsNone(maybe_export(self.store, run_id))

    def test_local_guard(self):
        self.assertTrue(is_local("http://127.0.0.1:4318"))
        self.assertTrue(is_local("http://localhost:4318"))
        self.assertFalse(is_local("http://collector.example.com:4318"))
        # A non-local endpoint is refused while local-only is the default.
        self.store.set_setting("telemetry.otlp_endpoint",
                               "http://collector.example.com:4318")
        run_id, plan = self.finished_run()
        self.assertIsNone(maybe_export(self.store, run_id))


class TestExport(TelemetryBase):
    def test_spans_follow_the_pinned_genai_convention(self):
        receiver = Receiver()
        self.addCleanup(receiver.stop)
        self.store.set_setting("telemetry.otlp_endpoint", receiver.endpoint)
        run_id, plan = self.finished_run()
        result = maybe_export(self.store, run_id)
        self.assertEqual(result["status"], 200)
        # run span + one span per task
        self.assertEqual(result["spans"], 1 + len(plan.tasks))
        body = receiver.bodies[0]
        resource = body["resourceSpans"][0]["resource"]["attributes"]
        names = {a["key"] for a in resource}
        self.assertIn("service.name", names)
        spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
        run_span = next(s for s in spans if s["name"] == "gen_ai.run")
        attrs = {a["key"]: a["value"] for a in run_span["attributes"]}
        self.assertEqual(attrs["las.run.id"]["stringValue"], run_id)
        self.assertEqual(attrs["las.telemetry.convention"]["stringValue"],
                         PINNED_CONVENTION)
        task_span = next(s for s in spans
                         if s["name"] == f"gen_ai.task {plan.tasks[0].id}")
        tattrs = {a["key"]: a["value"] for a in task_span["attributes"]}
        self.assertEqual(tattrs["gen_ai.operation.name"]["stringValue"],
                         "execute_task")
        # Every span sits on one trace derived from the run.
        self.assertEqual(len({s["traceId"] for s in spans}), 1)

    def test_unreachable_endpoint_never_raises(self):
        self.store.set_setting("telemetry.otlp_endpoint",
                               "http://127.0.0.1:1")   # nothing listens
        run_id, plan = self.finished_run()
        self.assertIsNone(maybe_export(self.store, run_id))   # prints, returns


if __name__ == "__main__":
    unittest.main()
