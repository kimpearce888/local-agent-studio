"""RUN-6: editing pending tasks. Edits are events; replay applies them to
the plan the run executes, so they survive crashes and replays."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.execution import Engine
from las.modules.storage import Store
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class EditTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()

    def events(self, run_id):
        return Store(self.data_dir).events(run_id)


class TestEditPendingTask(EditTestBase):
    def test_edit_is_an_event_and_replay_applies_it(self):
        plan = make_plan()
        engine = Engine(self.data_dir)
        run_id = engine.create_run(plan)
        new_instructions = ("Rewrite the summary with exact numbers and a "
                            "source list, twice as detailed as before.")
        code, out = self.run_cli("edit", run_id, "t2",
                                 "--instructions", new_instructions,
                                 "--reason", "sharpen the output")
        self.assertEqual(code, EXIT_OK, out)
        # The audit event exists...
        types = [e.type for e in self.events(run_id)]
        self.assertIn("task.edited", types)
        # ...replay applies it...
        state = engine.state(run_id)
        self.assertEqual(state.plan.tasks[1].instructions, new_instructions)
        self.assertTrue(state.tasks["t2"].edited)
        # ...and inspect shows the edit.
        code, out = self.run_cli("inspect", run_id)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("(edited)", out)

    def test_edited_instructions_reach_the_session(self):
        # The run must execute the edited instructions, not the originals.
        plan = make_plan()
        engine = Engine(self.data_dir)
        run_id = engine.create_run(plan)
        new_instructions = ("Answer with a numbered list of five findings "
                            "and cite each one explicitly.")
        engine.edit_task(run_id, "t2", {"instructions": new_instructions})
        from las.modules.providers.mock import MockAdapter
        from las.modules.providers import ProviderRegistry
        from las.modules.execution import DriverConfig
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter(
            [success_turn(plan, t.id) for t in plan.tasks]))
        driver = engine.driver(run_id, registry,
                               {"default": ("mock", "mock-model")},
                               DriverConfig(failure_policy="stop"))
        driver.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")
        hits = [e for e in self.events(run_id)
                if e.payload and e.payload.get("task_id") == "t2"
                and new_instructions[:30] in json.dumps(e.payload)]
        self.assertTrue(hits, "the edited instructions must reach task t2's session")

    def test_edit_after_pause_reaches_remaining_tasks(self):
        plan = make_plan()
        new_instructions = ("Collect every finding and write the final "
                            "report with a clear recommendation.")
        from las.modules.providers.mock import MockAdapter
        from las.modules.providers import ProviderRegistry
        from las.modules.execution import DriverConfig
        engine = Engine(self.data_dir)
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter(
            [success_turn(plan, t.id) for t in plan.tasks]))
        run_id = engine.create_run(plan)
        driver = engine.driver(run_id, registry,
                               {"default": ("mock", "mock-model")},
                               DriverConfig(failure_policy="stop"))
        driver.run(pause_after=plan.tasks[0].id)
        self.assertEqual(engine.state(run_id).status, "paused")
        engine.edit_task(run_id, "t2", {"instructions": new_instructions})
        # Resume is a control (controls.py pattern): append run.resumed, then
        # a fresh driver continues with the edited plan.
        from las.modules.storage import Store as _Store
        _Store(self.data_dir).append(run_id, "run.resumed",
                                     {"reason": "resume after edit"})
        driver2 = engine.driver(run_id, registry,
                                {"default": ("mock", "mock-model")},
                                DriverConfig(failure_policy="stop"))
        driver2.run()
        self.assertEqual(engine.state(run_id).status, "succeeded")
        self.assertEqual(engine.state(run_id).plan.tasks[1].instructions,
                         new_instructions)


class TestEditRefusals(EditTestBase):
    def test_unknown_task_refused(self):
        plan = make_plan()
        engine = Engine(self.data_dir)
        run_id = engine.create_run(plan)
        with self.assertRaises(ValueError):
            engine.edit_task(run_id, "nope", {"title": "X title that fits"})
        code, out = self.run_cli("edit", run_id, "nope", "--title", "New title")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("No task nope", out)

    def test_invalid_edit_refused_with_rule(self):
        plan = make_plan()
        engine = Engine(self.data_dir)
        run_id = engine.create_run(plan)
        # Instructions below the 20-character minimum violate the schema.
        with self.assertRaises(ValueError) as ctx:
            engine.edit_task(run_id, "t2", {"instructions": "too short"})
        self.assertIn("invalid", str(ctx.exception))

    def test_terminal_run_refused(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        with self.assertRaises(ValueError) as ctx:
            engine.edit_task(run_id, "t2", {"title": "A perfectly fine title"})
        self.assertIn("succeeded", str(ctx.exception))


class TestModelOverrideDisplay(EditTestBase):
    """PRV-6: per-task model overrides appear in the run record."""

    def test_inspect_shows_model_override(self):
        plan = make_plan()
        engine = Engine(self.data_dir)
        run_id = engine.create_run(plan)
        engine.edit_task(run_id, "t2",
                         {"model": {"provider_id": "server-b", "model_id": "qwen"}})
        code, out = self.run_cli("inspect", run_id)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("[model: server-b/qwen]", out)
        # And the task-level view repeats it.
        code, out = self.run_cli("inspect", run_id, "--task", "t2")
        self.assertIn("model override: server-b/qwen", out)


if __name__ == "__main__":
    unittest.main()


class TestDeleteRun(EditTestBase):
    """SET-2: delete one run (CLI confirmation + real deletion)."""

    def test_cli_delete_requires_confirm_and_deletes(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        self.assertIn(run_id, Store(self.data_dir).run_ids())
        code, out = self.run_cli("delete", run_id)
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("--confirm", out)
        self.assertIn(run_id, Store(self.data_dir).run_ids())
        code, out = self.run_cli("delete", run_id, "--confirm")
        self.assertEqual(code, EXIT_OK, out)
        self.assertNotIn(run_id, Store(self.data_dir).run_ids())
        self.assertFalse(os.path.exists(Store(self.data_dir).run_dir(run_id)))
        code, out = self.run_cli("delete", run_id, "--confirm")
        self.assertEqual(code, EXIT_INVALID)   # gone: unknown run

    def test_api_delete_run(self):
        import json as _json
        import threading
        import urllib.request
        from las.modules.api import serve
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        server, token, _ = serve(self.data_dir)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        req = urllib.request.Request(
            f"{base}/runs/{run_id}", headers={"X-Studio-Token": token},
            method="DELETE")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(json.loads(r.read().decode())["deleted"], run_id)
        req = urllib.request.Request(
            f"{base}/runs/{run_id}", headers={"X-Studio-Token": token})
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
