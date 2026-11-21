"""CLI smoke tests (spec 7.4) against the offline mock provider."""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from las.cli import (
    EXIT_CANCELLED, EXIT_CAP, EXIT_GATE, EXIT_INVALID, EXIT_OK, EXIT_PROVIDER,
    EXIT_RUN_FAILED, main,
)
from tests.util import constraint_objs, handoff_text, make_plan


class CliTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.plan_path = os.path.join(self.tmp.name, "plan.json")
        self.script_path = os.path.join(self.tmp.name, "mock_script.json")

    def tearDown(self):
        os.environ.pop("LAS_MOCK_SCRIPT", None)
        self.tmp.cleanup()

    def run_cli(self, *argv):
        """Invoke the CLI with a captured stdout; returns (exit_code, output)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()

    def write_plan(self, plan):
        with open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan.to_obj(), fh, ensure_ascii=False, indent=1)

    def write_script(self, turns):
        with open(self.script_path, "w", encoding="utf-8") as fh:
            json.dump(turns, fh)
        os.environ["LAS_MOCK_SCRIPT"] = self.script_path

    def happy_script(self, plan):
        return [{"text": handoff_text(t.id, f"Finished {t.id}.",
                                      constraint_objs(plan))}
                for t in plan.tasks]


class TestProviders(CliTestBase):
    def test_add_list(self):
        code, out = self.run_cli("providers", "add", "--name", "m", "--preset", "mock")
        self.assertEqual(code, EXIT_OK)
        code, out = self.run_cli("providers", "list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("m", out)
        self.assertIn("mock", out)

    def test_detect_reports_nothing_offline(self):
        # No servers on the standard ports in the sandbox; must not crash.
        code, out = self.run_cli("providers", "detect")
        self.assertEqual(code, EXIT_OK)


class TestInspection(CliTestBase):
    def _successful_run_id(self) -> str:
        code, _ = self.run_cli("providers", "add", "--name", "m", "--preset", "mock")
        self.assertEqual(code, EXIT_OK)
        plan = make_plan()
        self.write_plan(plan)
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("run", self.plan_path, "--provider", "m")
        self.assertEqual(code, EXIT_OK, out)
        return out.split("Run ")[1].split(" ")[0]

    def test_list_inspect_verify(self):
        rid = self._successful_run_id()

        code, out = self.run_cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn(rid, out)

        code, out = self.run_cli("inspect", rid)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("t3", out)

        code, out = self.run_cli("inspect", rid, "--task", "t1", "--json")
        self.assertEqual(code, EXIT_OK)
        obj = json.loads(out)
        self.assertEqual(obj["state"], "succeeded")

        code, out = self.run_cli("verify", rid)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("verified", out)


class TestRun(CliTestBase):
    def setUp(self):
        super().setUp()
        code, _ = self.run_cli("providers", "add", "--name", "mocktest", "--preset", "mock")
        self.assertEqual(code, EXIT_OK)

    def test_run_succeeds_with_mock_script(self):
        plan = make_plan()
        self.write_plan(plan)
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("run", self.plan_path, "--provider", "mocktest")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("Run succeeded", out)

    def test_run_invalid_plan_exits_2(self):
        plan = make_plan()
        plan.tasks[1].needs = ["t3"]          # V-03: dependency on a later task
        self.write_plan(plan)
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("run", self.plan_path, "--provider", "mocktest")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("V-03", out)

    def test_run_rule_of_two_blocks_with_l01(self):
        plan = make_plan()
        plan.tasks[0].tools = ["read_file", "web_fetch"]
        plan.tasks[1].tools = ["read_file", "run_command"]
        self.write_plan(plan)
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("run", self.plan_path, "--provider", "mocktest")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("L-01", out)

    def test_run_missing_plan_exits_2(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("run", os.path.join(self.tmp.name, "nope.json"),
                         "--provider", "mocktest")
        self.assertEqual(ctx.exception.code, EXIT_INVALID)

    def test_run_without_provider_exits_3(self):
        # Fresh data dir with no providers at all.
        plan = make_plan()
        self.write_plan(plan)
        empty = os.path.join(self.tmp.name, "empty-data")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["run", self.plan_path, "--data-dir", empty])
        self.assertEqual(code, EXIT_PROVIDER)

    def test_gate_flow_exit_4_then_approve_and_resume(self):
        plan = make_plan()
        plan.tasks[0].gate_before = True
        self.write_plan(plan)
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("run", self.plan_path, "--provider", "mocktest")
        self.assertEqual(code, EXIT_GATE, out)
        self.assertIn("GATE-REQUIRED", out)
        run_id = out.split("studio approve ")[1].split(" ")[0]

        code, out = self.run_cli("approve", run_id, "t1", "--reason", "reviewed")
        self.assertEqual(code, EXIT_OK)
        code, out = self.run_cli("resume", run_id, "--provider", "mocktest")
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn("Run succeeded", out)

    def test_failure_panel_exit_1_and_retry(self):
        plan = make_plan()
        self.write_plan(plan)
        # t1 never produces a handoff: HANDOFF-INVALID under the ask policy.
        self.write_script([
            {"text": "I will not produce a handoff."},
            {"text": "Still no handoff."},
        ] + self.happy_script(plan)[1:])
        code, out = self.run_cli("run", self.plan_path, "--provider", "mocktest",
                                 "--on-failure", "ask")
        self.assertEqual(code, EXIT_RUN_FAILED, out)
        self.assertIn("HANDOFF-INVALID", out)
        run_id = out.split("Run ")[1].split(" ")[0]

        # Retry t1 with a fresh script that succeeds.
        self.write_script(self.happy_script(plan))
        code, out = self.run_cli("retry", run_id, "t1")
        self.assertEqual(code, EXIT_OK)
        code, out = self.run_cli("resume", run_id, "--provider", "mocktest",
                                 "--on-failure", "stop")
        self.assertEqual(code, EXIT_OK, out)


class TestPlanCommand(CliTestBase):
    def test_plan_with_mock_provider(self):
        code, _ = self.run_cli("providers", "add", "--name", "m", "--preset", "mock")
        self.assertEqual(code, EXIT_OK)
        plan = make_plan()
        reply = {
            "title": "Planned brief",
            "constraints": [
                {"id": "c1", "text": "You must cite every figure.", "authority": "user"},
                {"id": "c2", "text": "Never invent numbers.", "authority": "user"},
            ],
            "tasks": [
                {"id": "t1", "title": "Gather", "type": "task",
                 "instructions": "Gather five sourced facts about the topic.",
                 "needs": []},
                {"id": "t2", "title": "Final", "type": "collector",
                 "instructions": "Combine everything into the final short brief.",
                 "needs": ["t1"]},
            ],
        }
        self.write_script([{"json": reply}])
        out_path = os.path.join(self.tmp.name, "planned.json")
        code, out = self.run_cli("plan", "Write a brief. You must cite every figure. "
                                          "Never invent numbers.",
                                 "--provider", "m", "-o", out_path)
        self.assertEqual(code, EXIT_OK, out)
        with open(out_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(len(saved["tasks"]), 2)
        self.assertEqual(saved["constraints"][0]["text"], "You must cite every figure.")


if __name__ == "__main__":
    unittest.main()
