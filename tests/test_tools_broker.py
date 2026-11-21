"""Permission broker and Rule of Two flags (spec 5.9, TLS-2, BLD-4)."""

import unittest

from las.schema.plan import Task
from las.tools import PermissionBroker, TOOL_FLAGS
from tests.util import make_plan
import tempfile
from las.workspace import Workspace


class TestFlags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.broker = PermissionBroker(Workspace(self.tmp.name))
        self.plan = make_plan()

    def tearDown(self):
        self.tmp.cleanup()

    def flags_for(self, tools, needs=(), input_flags=None, gate=False):
        task = Task(id="t9", title="T", instructions="Do the thing carefully.",
                    tools=list(tools), needs=list(needs), gate_before=gate)
        return self.broker.compute_flags(task, self.plan, input_flags or {})

    def test_workspace_tools_set_no_flags(self):
        self.assertEqual(self.flags_for(["read_file", "write_file"]), (False, False, False))

    def test_web_fetch_sets_a_and_c(self):
        self.assertEqual(self.flags_for(["web_fetch"]), (True, False, True))

    def test_run_command_sets_b_and_c(self):
        self.assertEqual(self.flags_for(["run_command"]), (False, True, True))

    def test_unknown_tool_is_worst_case(self):
        self.assertEqual(self.flags_for(["mystery"]), (True, True, True))

    def test_a_propagates_through_needs(self):
        # t1 used web_fetch -> A.  A task needing t1 inherits A.
        self.assertEqual(
            self.flags_for(["read_file"], needs=["t1"],
                           input_flags={"t1": (True, False, True)}),
            (True, False, False))

    def test_a_cleared_by_approved_gate(self):
        task = Task(id="t9", title="T", instructions="Do the thing carefully.",
                    tools=["read_file"], needs=["t1"], gate_before=True)
        flags = self.broker.compute_flags(task, self.plan,
                                          {"t1": (True, False, True)}, {"t1"})
        self.assertEqual(flags, (False, False, False))


class TestBrokerDecisions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.task = Task(id="t1", title="T", instructions="Do the thing carefully.",
                         tools=["read_file", "write_file", "run_command", "web_fetch"])

    def tearDown(self):
        self.tmp.cleanup()

    def broker(self, **kwargs):
        return PermissionBroker(self.ws, **kwargs)

    def test_not_in_allowlist(self):
        broker = self.broker()
        task = Task(id="t1", title="T", instructions="Do the thing carefully.",
                    tools=["read_file"])
        decision = broker.check("write_file", {"path": "a.md", "content": "x"}, task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "not-in-allowlist")

    def test_unknown_tool(self):
        decision = self.broker().check("explode", {}, self.task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "unknown-tool")

    def test_bad_args(self):
        decision = self.broker().check("write_file", {"path": "a.md"}, self.task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "bad-args")

    def test_shell_off_by_default(self):
        decision = self.broker().check("run_command", {"command": ["ls"]}, self.task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "shell-disabled")

    def test_shell_allowlist(self):
        broker = self.broker(enable_shell=True, command_allowlist=["ls"])
        ok = broker.check("run_command", {"command": ["ls", "-la"]}, self.task)
        self.assertTrue(ok.allowed)
        denied = broker.check("run_command", {"command": ["rm", "-rf", "/"]}, self.task)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.rule, "command-not-allowlisted")

    def test_web_off_by_default(self):
        decision = self.broker().check("web_fetch", {"url": "https://example.com"}, self.task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "web-disabled")

    def test_web_domain_allowlist(self):
        broker = self.broker(enable_web=True, web_domains=["example.com"])
        ok = broker.check("web_fetch", {"url": "https://example.com/x"}, self.task)
        self.assertTrue(ok.allowed)
        denied = broker.check("web_fetch", {"url": "https://evil.com/x"}, self.task)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.rule, "domain-not-allowlisted")

    def test_execute_write_and_read(self):
        broker = self.broker()
        write = broker.execute("write_file", {"path": "a.md", "content": "hi"}, self.task)
        self.assertTrue(write.allowed)
        read = broker.execute("read_file", {"path": "a.md"}, self.task)
        self.assertTrue(read.allowed)
        self.assertIn("hi", read.result)

    def test_execute_jail_error(self):
        broker = self.broker()
        decision = broker.execute("write_file", {"path": "../escape.txt", "content": "x"},
                                  self.task)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule, "path-escape")

    def test_run_command_executes_and_scrubs_env(self):
        broker = self.broker(enable_shell=True, command_allowlist=["sh"])
        decision = broker.execute(
            "run_command", {"command": ["sh", "-c", "echo $HOME; pwd"]}, self.task)
        self.assertTrue(decision.allowed)
        self.assertNotIn("/home/", decision.result)     # HOME points into the jail


class TestToolFlagsTable(unittest.TestCase):
    def test_every_known_tool_has_flags(self):
        from las.tools import KNOWN_TOOLS
        for tool in KNOWN_TOOLS:
            self.assertIn(tool, TOOL_FLAGS)


if __name__ == "__main__":
    unittest.main()
