#!/usr/bin/env python3
"""Insert the bare-JSON parser regression tests (transport-safe)."""

PATH = "/home/z/my-project/local-agent-studio/tests/test_context.py"
src = open(PATH, encoding="utf-8").read()

anchor = "def _handoff(task_id: str, summary: str, facts=None) -> Handoff:"
assert src.count(anchor) == 1, "anchor not unique"

new_tests = '''    def test_parse_bare_json_tool_call(self):
        # What the reference model emitted once text mode was wired
        # (live run 2026-11-22): the documented JSON line, bare - the
        # parser used to require the wrapped form and executed nothing.
        parsed = parse_text_tool_call(
            '{"name": "list_files", "arguments": {}}')
        self.assertEqual(parsed[0], "list_files")
        self.assertEqual(parsed[1], {})
        parsed = parse_text_tool_call(
            'I will read the sales file.\\n'
            '{"name": "read_file", "arguments": '
            '{"path": "sales-q3-2026.csv"}}')
        self.assertEqual(parsed[0], "read_file")
        self.assertEqual(parsed[1], {"path": "sales-q3-2026.csv"})

    def test_handoff_message_json_is_not_a_tool_call(self):
        # The guard: a final handoff message contains JSON too; it must
        # be treated as the final message, never as a tool call.
        obj = ('{"task_id": "t1", "status": "done", "summary": "x", '
               '"key_facts": [{"fact": "f", "source": "file:a"}], '
               '"binding_constraints": [], "decisions": [], '
               '"open_issues": [], "deliverables": []}')
        text = "Finished.\\n<handoff>\\n" + obj + "\\n</handoff>"
        self.assertIsNone(parse_text_tool_call(text))


'''
idx = src.index(anchor)
# back up to the start of the preceding blank separator
insert_at = src.rindex("\n\n\n", 0, idx) + 3
src = src[:insert_at] + new_tests + src[insert_at:]
open(PATH, "w", encoding="utf-8").write(src)
print("tests inserted at", insert_at)
