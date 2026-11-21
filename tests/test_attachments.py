"""PLN-7 / TLS-8: attachments - import, planner manifest, workspace copy;
LIB-3: role-profile prompt version history."""

import os
import tempfile
import unittest

from las.modules.attachments import (AttachmentError, copy_to_workspace,
                                     extract_text, import_file, manifest_block)
from las.modules.library import save_role_profile
from las.modules.storage import Store
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class AttachmentsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self.store = Store(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path


class TestImport(AttachmentsBase):
    def test_text_files_import_with_summary(self):
        md = self.write("notes.md", "# Notes\n" + "line of facts\n" * 40)
        entry = import_file(md, self.data_dir, "p1")
        self.assertTrue(entry["supported"])
        self.assertEqual(entry["name"], "notes.md")
        self.assertLessEqual(len(entry["summary"]), 403)
        self.assertIn("# Notes", entry["summary"])
        self.assertTrue(os.path.exists(
            os.path.join(self.data_dir, "attachments", "p1", "notes.md")))

    def test_csv_preview_not_whole_file(self):
        csv_path = self.write("table.csv", "a,b\n1,2\n" * 50)
        entry = import_file(csv_path, self.data_dir, "p1")
        self.assertIn("more rows", entry["summary"])
        self.assertLess(len(entry["summary"]), 400)

    def test_pdf_refused_honestly(self):
        pdf = self.write("doc.pdf", "%PDF-1.4 fake")
        with self.assertRaises(AttachmentError) as ctx:
            import_file(pdf, self.data_dir, "p1")
        self.assertIn("desktop importer", str(ctx.exception))
        supported, reason = extract_text(pdf)
        self.assertFalse(supported)

    def test_manifest_block_never_contains_whole_files(self):
        long_text = "x" * 5000
        entry = import_file(self.write("big.txt", long_text), self.data_dir, "p1")
        block = manifest_block([entry])
        self.assertNotIn("x" * 500, block)
        self.assertIn("big.txt", block)
        self.assertIn("sha256", block)

    def test_workspace_copy(self):
        import_file(self.write("notes.md", "hello"), self.data_dir, "p1")
        copied = copy_to_workspace(self.data_dir, "p1",
                                   os.path.join(self.data_dir, "ws"))
        self.assertEqual(copied, ["notes.md"])
        self.assertEqual(copied, os.listdir(os.path.join(self.data_dir, "ws")))


class TestPromptHistory(AttachmentsBase):
    def test_changed_prompt_keeps_revisions(self):
        save_role_profile(self.store, {"id": "r", "name": "R", "role": "worker",
                                       "system_prompt": "Be terse."})
        save_role_profile(self.store, {"id": "r", "name": "R", "role": "worker",
                                       "system_prompt": "Be terse and cite."})
        profile = self.store.get_role_profile("r")
        self.assertEqual(profile["system_prompt"], "Be terse and cite.")
        self.assertEqual(len(profile["revisions"]), 1)
        self.assertEqual(profile["revisions"][0]["system_prompt"], "Be terse.")
        # Same prompt saved again: no duplicate revision.
        save_role_profile(self.store, {"id": "r", "name": "R", "role": "worker",
                                       "system_prompt": "Be terse and cite."})
        self.assertEqual(len(self.store.get_role_profile("r")["revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
