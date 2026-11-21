"""Diagnostics module tests (OBS-7, spec S13): redaction guarantees."""

import json
import os
import tempfile
import unittest

from las.modules.diagnostics import build_diagnostics, diagnostics_items, text_meta
from las.modules.execution import DriverConfig, Engine
from las.modules.library import builtin_templates, template_entry
from las.modules.storage import Store
from las.shared.provider_contract import ProviderProfile
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class DiagnosticsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self.store = Store(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()


class TestRedaction(DiagnosticsTestBase):
    def test_text_meta_never_contains_text(self):
        meta = text_meta("secret goal text")
        self.assertNotIn("secret", json.dumps(meta))
        self.assertEqual(meta["length"], len("secret goal text"))
        self.assertEqual(len(meta["sha256"]), 16)

    def test_bundle_redacts_goal_and_never_includes_prompts(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        self.store.save_provider(ProviderProfile(
            id="p1", name="Server", kind="generic", auth_env="env:MY_KEY"))
        bundle = build_diagnostics(self.store, Engine(self.data_dir), [
            {"id": "storage", "version": "1.0.0", "core": True,
             "enabled": True, "summary": "s"}])
        dumped = json.dumps(bundle, ensure_ascii=False)
        # The goal text itself never appears...
        self.assertNotIn(plan.goal[:30], dumped)
        # ...but its length and hash do.
        run = next(r for r in bundle["runs"] if r["run_id"] == run_id)
        self.assertEqual(run["goal"]["length"], len(plan.goal))
        self.assertGreater(len(run["goal"]["sha256"]), 0)
        # Keys are never present, only whether a reference exists.
        self.assertNotIn("MY_KEY", dumped)
        self.assertTrue(bundle["providers"][0]["auth_env_reference"])
        # Chain verification and module state are included.
        self.assertTrue(run["chain_verified"])
        self.assertEqual(bundle["modules"][0]["id"], "storage")
        self.assertGreater(run["events"], 5)

    def test_diagnostics_items_lists_everything(self):
        items = diagnostics_items()
        self.assertTrue(any("SHA-256" in i for i in items))
        self.assertTrue(any("never the key" in i for i in items))


class TestDataLifecycle(DiagnosticsTestBase):
    def test_prune_and_delete_all(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        self.assertIn(run_id, self.store.run_ids())
        # Retention 0 days prunes everything (spec S11 values: keep forever,
        # 90 days, 30 days - 0 for the test).
        pruned = self.store.prune_runs(0)
        self.assertEqual(pruned, [run_id])
        self.assertNotIn(run_id, self.store.run_ids())
        self.assertFalse(os.path.exists(self.store.run_dir(run_id)))

    def test_prune_keeps_recent_runs(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        self.assertEqual(self.store.prune_runs(90), [])
        self.assertIn(run_id, self.store.run_ids())

    def test_delete_all_data_keeps_providers_and_settings(self):
        plan = make_plan()
        engine, run_id, driver, results = run_with_mock(
            plan, [success_turn(plan, t.id) for t in plan.tasks], self.data_dir)
        self.store.save_provider(ProviderProfile(id="p", name="P", kind="mock"))
        self.store.delete_all_data()
        self.assertEqual(self.store.run_ids(), [])
        self.assertEqual(len(self.store.list_providers()), 1)


if __name__ == "__main__":
    unittest.main()
