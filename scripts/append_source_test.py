#!/usr/bin/env python3
"""Append the H-06 verbose-prefix regression test (transport-safe)."""

PATH = "/home/z/my-project/local-agent-studio/tests/test_blocked_runs.py"
src = open(PATH, encoding="utf-8").read()
anchor = "if __name__ == \"__main__\":"
assert src.count(anchor) == 1

new_test = '''class SourceLeniencyTests(unittest.TestCase):
    """H-06 accepts verbose file-path prefixes (measured: 'workspace
    file: a.csv' killed an otherwise perfect handoff twice)."""

    def test_verbose_prefix_resolves(self):
        import tempfile
        from las.modules.handoffs import _source_ok
        from las.modules.security import Workspace
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.write_file("sales-q3-2026.csv", "region\\n")
            self.assertTrue(_source_ok("sales-q3-2026.csv", ws))
            self.assertTrue(
                _source_ok("workspace file: sales-q3-2026.csv", ws))
            self.assertTrue(
                _source_ok("workspace: sales-q3-2026.csv", ws))
            self.assertTrue(_source_ok("tool:call_1", ws))
            self.assertFalse(_source_ok("workspace files", ws))
            self.assertFalse(_source_ok("", ws))


'''
idx = src.index(anchor)
src = src[:idx] + new_test + src[idx:]
open(PATH, "w", encoding="utf-8").write(src)
print("test appended")
