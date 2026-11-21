"""Workspace jail: path traversal, symlinks, size limits (TLS-1)."""

import os
import unittest

from las.workspace import Workspace, WorkspaceError


class WorkspaceTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class TestJail(unittest.TestCase):
    """The jail root is a subfolder, so 'outside' is truly outside."""

    def setUp(self):
        import tempfile
        import shutil
        self._tmp = tempfile.mkdtemp(prefix="las-jail-")
        self.root = os.path.join(self._tmp, "ws")
        os.makedirs(self.root)
        self.outside = os.path.join(self._tmp, "outside")
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "secret.txt"), "w") as fh:
            fh.write("secret")
        self.ws = Workspace(self.root)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp)

    def test_write_and_read(self):
        self.ws.write_file("notes/facts.md", "hello")
        self.assertEqual(self.ws.read_file("notes/facts.md"), "hello")

    def test_absolute_path_rejected(self):
        with self.assertRaises(WorkspaceError) as ctx:
            self.ws.resolve("/etc/passwd")
        self.assertEqual(ctx.exception.rule, "path-escape")

    def test_dotdot_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("../outside.txt")
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("sub/../../outside.txt")

    def test_windows_drive_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("C:/windows/system32")

    def test_empty_path_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("")

    def test_symlink_escape_rejected(self):
        link = os.path.join(self.root, "link")
        os.symlink(self.outside, link)
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("link/secret.txt")
        with self.assertRaises(WorkspaceError):
            self.ws.write_file("link/evil.txt", "boom")

    def test_symlink_inside_jail_ok(self):
        self.ws.write_file("real.md", "content")
        os.symlink(os.path.join(self.root, "real.md"),
                   os.path.join(self.root, "alias.md"))
        self.assertEqual(self.ws.read_file("alias.md"), "content")

    def test_read_missing_file(self):
        with self.assertRaises(WorkspaceError):
            self.ws.read_file("nope.md")


class TestSizeLimits(WorkspaceTestBase):
    def test_file_size_limit(self):
        big = "x" * (5 * 1024 * 1024 + 10)
        with self.assertRaises(WorkspaceError) as ctx:
            self.ws.write_file("big.txt", big)
        self.assertEqual(ctx.exception.rule, "size-limit")

    def test_total_size_limit(self):
        small_ws = Workspace(self.tmp.name, max_total_bytes=1000)
        with self.assertRaises(WorkspaceError):
            small_ws.write_file("a.txt", "x" * 600)
            small_ws.write_file("b.txt", "x" * 600)


class TestTools(WorkspaceTestBase):
    def test_list_files(self):
        self.ws.write_file("a.md", "1")
        self.ws.write_file("sub/b.md", "2")
        self.assertEqual(self.ws.list_files(), ["a.md", "sub/b.md"])

    def test_append(self):
        self.ws.write_file("log.txt", "one\n")
        self.ws.write_file("log.txt", "two\n", append=True)
        self.assertEqual(self.ws.read_file("log.txt"), "one\ntwo\n")

    def test_search(self):
        self.ws.write_file("a.md", "solar grew 30 percent")
        self.ws.write_file("b.md", "wind doubled")
        hits = self.ws.search_files(r"solar|wind")
        self.assertEqual({h["path"] for h in hits}, {"a.md", "b.md"})

    def test_patch(self):
        self.ws.write_file("a.md", "alpha beta gamma")
        result = self.ws.patch_file("a.md", "beta", "delta")
        self.assertIn("replaced 1", result)
        self.assertEqual(self.ws.read_file("a.md"), "alpha delta gamma")

    def test_manifest(self):
        self.ws.write_file("a.md", "12345")
        m = self.ws.manifest()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["path"], "a.md")
        self.assertEqual(m[0]["size"], 5)
        self.assertEqual(len(m[0]["sha256"]), 64)

    def test_manifest_truncates(self):
        for i in range(60):
            self.ws.write_file(f"f{i:03}.txt", "x")
        self.assertEqual(len(self.ws.manifest()), 60)
        self.assertEqual(len(self.ws.manifest(50)), 50)


if __name__ == "__main__":
    unittest.main()
