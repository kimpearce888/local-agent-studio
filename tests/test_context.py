"""Context assembly: block order, isolation, budget, compaction (spec 5.4)."""

import unittest

from las.context import (
    WORKER_PROTOCOL, assemble_context, estimate_tokens, parse_text_tool_call,
)
from las.schema.handoff import Handoff, KeyFact
from las.tools import PermissionBroker
from las.workspace import Workspace
from tests.planlib import make_plan
import tempfile


class ContextTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)
        self.broker = PermissionBroker(self.ws)
        self.plan = make_plan()
        self.ws.write_file("facts.md", "fact one\nfact two\n")

    def tearDown(self):
        self.tmp.cleanup()

    def assemble(self, task_id: str, handoffs=None, **kwargs):
        task = self.plan.task(task_id)
        kwargs.setdefault("context_length", 8192)
        return assemble_context(task, self.plan, handoffs or {}, self.ws, self.broker,
                                **kwargs)


class TestBlockOrder(ContextTestBase):
    def test_system_message_contains_protocol_and_constraints(self):
        ctx = self.assemble("t1")
        system = ctx.messages[0].content
        self.assertEqual(ctx.messages[0].role, "system")
        self.assertIn("WORKER PROTOCOL", system)
        self.assertIn("BINDING CONSTRAINTS", system)
        # Constraints copied verbatim with their metadata.
        self.assertIn("[c1] (state: active) You must cite every figure.", system)
        self.assertIn("[c2] (state: active) Never invent numbers.", system)

    def test_user_message_blocks_in_order(self):
        ctx = self.assemble("t2", handoffs={"t1": _handoff("t1", "CANARY-XYZ in summary")})
        user = ctx.messages[1].content
        self.assertEqual(ctx.messages[1].role, "user")
        self.assertLess(user.index("GOAL:"), user.index("TASK t2:"))
        self.assertLess(user.index("TASK t2:"), user.index("=== Input from task t1"))
        self.assertLess(user.index("=== Input from task t1"),
                        user.index("WORKSPACE FILES"))

    def test_workspace_manifest_included(self):
        ctx = self.assemble("t1")
        self.assertIn("facts.md", ctx.messages[1].content)

    def test_isolation_no_unneeded_handoff(self):
        # AC-04 at the context level: t3 does not need t1's handoff if it only
        # lists t2... in the default plan t3 needs both; use a task with no needs.
        ctx = self.assemble("t1", handoffs={"t2": _handoff("t2", "SECRET-FROM-T2")})
        self.assertNotIn("SECRET-FROM-T2", ctx.messages[1].content)
        self.assertNotIn("=== Input from task", ctx.messages[1].content)

    def test_needed_handoff_rendered_with_source_label(self):
        ctx = self.assemble("t2", handoffs={"t1": _handoff("t1", "solar grew 30%")})
        user = ctx.messages[1].content
        self.assertIn("=== Input from task t1 (status: done) ===", user)
        self.assertIn("solar grew 30%", user)

    def test_constraints_not_repeated_in_inputs(self):
        ctx = self.assemble("t2", handoffs={"t1": _handoff("t1", "summary")})
        user = ctx.messages[1].content
        self.assertNotIn("[c1]", user)     # constraints live in block 2 only

    def test_breakdown_sections(self):
        ctx = self.assemble("t1")
        for key in ("reserved_output", "system_block", "binding_constraints", "goal",
                    "instructions", "handoffs", "workspace_manifest", "tool_definitions"):
            self.assertIn(key, ctx.breakdown)


class TestBudgetAndCompaction(ContextTestBase):
    def test_no_overflow_with_generous_context(self):
        ctx = self.assemble("t1")
        self.assertFalse(ctx.overflow)
        self.assertEqual(ctx.compaction_steps, [])

    def test_overflow_when_context_too_small(self):
        # A tiny context cannot fit even after the ladder -> overflow, and the
        # attempt must never start (RUN-9).
        ctx = self.assemble("t1", context_length=50)
        self.assertTrue(ctx.overflow)

    def test_compaction_step1_shortens_summaries(self):
        big_summary = "word " * 500
        calls = []

        def summarizer(text, max_words):
            calls.append((len(text.split()), max_words))
            return "short summary"

        ctx = self.assemble("t2", handoffs={"t1": _handoff("t1", big_summary)},
                            context_length=1200, summarizer=summarizer)
        self.assertIn("step1-shortened-summaries", ctx.compaction_steps)
        self.assertEqual(len(calls), 1)
        # The summary was replaced by the shortened one.
        self.assertIn("short summary", ctx.messages[1].content)
        # Constraint text is never compacted away.
        self.assertIn("[c1] (state: active) You must cite every figure.",
                      ctx.messages[0].content)

    def test_compaction_step2_moves_facts(self):
        facts = [KeyFact(f"fact number {i} about the market", "message:1")
                 for i in range(40)]
        handoff = Handoff(task_id="t1", status="done",
                          summary="short " * 30, key_facts=facts)
        ctx = self.assemble("t2", handoffs={"t1": handoff}, context_length=900)
        self.assertIn("step2-moved-facts", ctx.compaction_steps)
        # The moved facts live in a workspace file with a pointer.
        self.assertTrue(any("facts" in e["path"] for e in self.ws.manifest()))
        user = ctx.messages[1].content
        self.assertIn(".compaction/facts_t2.md", user)
        # The goal, task block and constraints survive every step.
        self.assertIn("GOAL:", user)
        self.assertIn("TASK t2:", user)
        self.assertIn("[c1]", ctx.messages[0].content)

    def test_never_compact_blocks_survive(self):
        facts = [KeyFact(f"filler fact {i}", "message:1") for i in range(60)]
        handoff = Handoff(task_id="t1", status="done", summary="word " * 400,
                          key_facts=facts)
        ctx = self.assemble("t2", handoffs={"t1": handoff}, context_length=1000)
        system = ctx.messages[0].content
        user = ctx.messages[1].content
        self.assertIn("Never invent numbers.", system)
        self.assertIn(self.plan.goal[:40], user)
        self.assertIn("Instructions:", user)


class TestEstimate(unittest.TestCase):
    def test_estimate_tokens_scales_and_nonzero(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("hello world"), 0)
        longer = estimate_tokens("x" * 3500)
        self.assertGreater(longer, 1000)

    def test_parse_text_tool_call(self):
        parsed = parse_text_tool_call(
            'thinking...\n<tool_call>{"name": "write_file", '
            '"arguments": {"path": "a.md", "content": "x"}}</tool_call>')
        self.assertEqual(parsed[0], "write_file")
        self.assertEqual(parsed[1]["path"], "a.md")

    def test_parse_text_tool_call_malformed(self):
        self.assertIsNone(parse_text_tool_call("<tool_call>{broken json}</tool_call>"))
        self.assertIsNone(parse_text_tool_call("no tool call here"))


def _handoff(task_id: str, summary: str, facts=None) -> Handoff:
    return Handoff(task_id=task_id, status="done", summary=summary,
                   key_facts=facts or [KeyFact("a fact", "message:1")])


if __name__ == "__main__":
    unittest.main()
