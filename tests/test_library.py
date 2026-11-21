"""Library module tests (LIB-1 templates, LIB-2 role profiles)."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.library import (
    LibraryError, builtin_templates, delete_template, duplicate_template,
    ensure_builtin_templates, save_role_profile, save_template, template_entry,
)
from las.modules.storage import Store
from tests.planlib import make_plan


class LibraryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self.store = Store(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()


class TestTemplates(LibraryTestBase):
    def test_builtins_seed_once_and_cannot_be_deleted(self):
        ensure_builtin_templates(self.store)
        ids = {t["id"] for t in self.store.list_templates()}
        # The four built-ins LIB-1 names.
        self.assertEqual(ids, {"builtin-research-report",
                               "builtin-document-analysis",
                               "builtin-code-review",
                               "builtin-content-pipeline"})
        # Idempotent.
        ensure_builtin_templates(self.store)
        self.assertEqual(len(self.store.list_templates()), len(builtin_templates()))
        with self.assertRaises(LibraryError) as ctx:
            delete_template(self.store, "builtin-research-report")
        self.assertIn("cannot be deleted", str(ctx.exception))

    def test_builtin_templates_validate(self):
        from las.modules.planquality import validate_plan
        from las.shared.plan_contract import Plan
        for tpl in builtin_templates():
            issues = validate_plan(Plan.from_obj(tpl["plan"]), variables={
                v: "x" for v in tpl["plan"].get("options", {})})
            # Variables are the point of templates: only V-12 may fire.
            self.assertTrue(all(i.rule == "V-12" for i in issues), (tpl["id"], issues))

    def test_save_template_validates_and_counts_variables(self):
        plan = make_plan()
        plan.tasks[0].title = "Work on {{topic}}"
        entry = save_template(self.store, plan.to_obj(), "My template",
                              "A test template")
        self.assertEqual(entry["task_count"], len(plan.tasks))
        self.assertIn("topic", entry["variables"])
        # An invalid plan is refused.
        bad = plan.to_obj()
        bad["tasks"] = []
        with self.assertRaises(LibraryError):
            template_entry(bad, "Bad", "")

    def test_duplicate_builtin_creates_user_copy(self):
        ensure_builtin_templates(self.store)
        entry = duplicate_template(self.store, "builtin-code-review",
                                   "My sanity check")
        self.assertFalse(entry["builtin"])
        self.assertIn("My sanity check",
                      [t["name"] for t in self.store.list_templates()])
        delete_template(self.store, entry["id"])   # user copies are deletable

    def test_cli_templates_roundtrip(self):
        plan = make_plan()
        plan_path = os.path.join(self.tmp.name, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan.to_obj(), fh)
        code, out = self.run_cli("templates", "save", plan_path,
                                 "--name", "Mine", "--description", "d")
        self.assertEqual(code, EXIT_OK, out)
        code, out = self.run_cli("templates", "list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Mine", out)
        self.assertIn("builtin", out)          # builtins seeded on list
        code, out = self.run_cli("templates", "list", "--json")
        rows = json.loads(out)
        self.assertTrue(any(r["id"].startswith("builtin-") for r in rows))
        # Deleting a builtin via CLI is refused with an actionable message.
        code, out = self.run_cli("templates", "delete", "builtin-research-report")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("cannot be deleted", out)
        # Templates are runnable: run accepts a template id (spec S10 Use).
        code, out = self.run_cli("run", "builtin-code-review")
        self.assertNotEqual(code, 0)   # no provider configured yet: refused, not crashed


class TestRoleProfiles(LibraryTestBase):
    def test_save_list_delete(self):
        entry = save_role_profile(self.store, {
            "id": "concise-worker", "name": "Concise worker", "role": "worker",
            "system_prompt": "Be terse.", "params": {"temperature": 0.2},
            "tools": ["read_file", "write_file"],
            "default_model": {"provider_id": "m", "model_id": "x"}})
        self.assertEqual(entry["role"], "worker")
        self.assertEqual(self.store.list_role_profiles()[0]["id"], "concise-worker")
        self.store.delete_role_profile("concise-worker")
        self.assertEqual(self.store.list_role_profiles(), [])

    def test_validation(self):
        with self.assertRaises(LibraryError):
            save_role_profile(self.store, {"id": "x", "name": "X", "role": "wizard"})
        with self.assertRaises(LibraryError):
            save_role_profile(self.store, {
                "id": "x", "name": "X", "role": "worker", "tools": ["hack"]})

    def test_cli_roles_roundtrip(self):
        profile_path = os.path.join(self.tmp.name, "role.json")
        with open(profile_path, "w", encoding="utf-8") as fh:
            json.dump({"id": "r1", "name": "R", "role": "collector",
                       "tools": ["read_file"]}, fh)
        code, out = self.run_cli("roles", "save", profile_path)
        self.assertEqual(code, EXIT_OK, out)
        code, out = self.run_cli("roles", "list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("r1", out)
        code, out = self.run_cli("roles", "delete", "r1")
        self.assertEqual(code, EXIT_OK)
        code, out = self.run_cli("roles", "list")
        self.assertNotIn("r1", out)


if __name__ == "__main__":
    unittest.main()
