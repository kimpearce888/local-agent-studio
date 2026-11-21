"""Module lifecycle tests (RFI section 11.4-11.5: disabled-state,
failure isolation). Disablement is enforced at authoritative boundaries
(the registry and the command handlers), never by hiding a UI entry.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stdout

from las.app.capabilities import set_enabled
from las.app.registry import ModuleRegistry
from las.app.cli import EXIT_INVALID, EXIT_OK, main
from las.modules.storage import Store
from tests.planlib import constraint_objs, handoff_text, make_plan


def module_installed(module_id: str) -> bool:
    try:
        ModuleRegistry().get(module_id)
        return True
    except Exception:  # noqa: BLE001 - ModuleNotInstalledError
        return False


class LifecycleTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.plan_path = os.path.join(self.tmp.name, "plan.json")
        self.script_path = os.path.join(self.tmp.name, "script.json")

    def tearDown(self):
        os.environ.pop("LAS_MOCK_SCRIPT", None)
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([*argv, "--data-dir", self.data_dir])
        return code, buf.getvalue()

    def write_plan(self, plan):
        with open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan.to_obj(), fh, ensure_ascii=False, indent=1)

    def write_happy_script(self, plan):
        turns = [{"text": handoff_text(t.id, f"Finished {t.id}.",
                                       constraint_objs(plan))}
                 for t in plan.tasks]
        with open(self.script_path, "w", encoding="utf-8") as fh:
            json.dump(turns, fh)
        os.environ["LAS_MOCK_SCRIPT"] = self.script_path

    def add_mock_provider(self):
        code, out = self.run_cli("providers", "add", "--name", "m", "--preset", "mock")
        self.assertEqual(code, EXIT_OK, out)

    def disable(self, module_id):
        store = Store(self.data_dir)
        set_enabled(ModuleRegistry(), module_id, False,
                    store.get_setting, store.set_setting)

    def enable(self, module_id):
        store = Store(self.data_dir)
        set_enabled(ModuleRegistry(), module_id, True,
                    store.get_setting, store.set_setting)

    def successful_run(self) -> str:
        """Run a plan through the CLI; returns the run id."""
        self.add_mock_provider()
        plan = make_plan()
        self.write_plan(plan)
        self.write_happy_script(plan)
        code, out = self.run_cli("run", self.plan_path, "--provider", "m")
        self.assertEqual(code, EXIT_OK, out)
        return out.split("Run ")[1].split(" ")[0]


@unittest.skipUnless(module_installed("reporting"), "reporting module not installed")
@unittest.skipUnless(module_installed("providers.mock"),
                       "fixture uses the providers.mock module")
class TestDisableReporting(LifecycleTestBase):
    def test_disabled_reporting_blocks_export_with_fix(self):
        rid = self.successful_run()
        self.disable("reporting")
        out_path = os.path.join(self.tmp.name, "r.md")
        code, out = self.run_cli("export", rid, "--format", "markdown", "-o", out_path)
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("reporting", out)
        self.assertIn("disabled", out)
        self.assertIn("studio modules enable reporting", out)
        self.assertFalse(os.path.exists(out_path), "no export file may be written")

    def test_disabled_reporting_does_not_break_runs_or_data(self):
        rid = self.successful_run()
        self.disable("reporting")
        # Unrelated functionality keeps working (failure isolation).
        plan = make_plan()
        self.write_plan(plan)
        self.write_happy_script(plan)
        code, out = self.run_cli("run", self.plan_path, "--provider", "m")
        self.assertEqual(code, EXIT_OK, out)
        code, out = self.run_cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn(rid, out)
        code, out = self.run_cli("verify", rid)
        self.assertEqual(code, EXIT_OK)
        # Data is never deleted by disablement.
        store = Store(self.data_dir)
        self.assertIn(rid, store.run_ids())

    def test_reenable_restores_export(self):
        rid = self.successful_run()
        self.disable("reporting")
        self.enable("reporting")
        out_path = os.path.join(self.tmp.name, "r.md")
        code, out = self.run_cli("export", rid, "-o", out_path)
        self.assertEqual(code, EXIT_OK, out)
        self.assertIn(rid, open(out_path, encoding="utf-8").read())


@unittest.skipUnless(module_installed("providers.mock"), "providers.mock module not installed")
class TestDisableMock(LifecycleTestBase):
    @unittest.skipUnless(module_installed("selftest"), "selftest module not installed")
    def test_disabled_mock_blocks_selftest(self):
        self.disable("providers.mock")
        code, out = self.run_cli("selftest")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("providers.mock", out)
        self.assertIn("studio modules enable providers.mock", out)

    def test_disabled_mock_blocks_run_with_mock_provider(self):
        self.add_mock_provider()
        plan = make_plan()
        self.write_plan(plan)
        self.write_happy_script(plan)
        self.disable("providers.mock")
        code, out = self.run_cli("run", self.plan_path, "--provider", "m")
        self.assertEqual(code, EXIT_INVALID)
        self.assertIn("providers.mock", out)

    def test_disabled_mock_leaves_wire_providers_usable(self):
        # The providers module itself stays importable and listable.
        self.disable("providers.mock")
        code, out = self.run_cli("modules", "list", "--json")
        self.assertEqual(code, EXIT_OK)
        rows = json.loads(out)
        by_id = {r["id"]: r for r in rows}
        self.assertFalse(by_id["providers.mock"]["enabled"])
        self.assertTrue(by_id["providers"]["enabled"])


class TestCoreProtection(LifecycleTestBase):
    def test_core_module_cannot_be_disabled(self):
        store = Store(self.data_dir)
        with self.assertRaises(ValueError) as ctx:
            set_enabled(ModuleRegistry(), "providers", False,
                        store.get_setting, store.set_setting)
        self.assertIn("required by the application", str(ctx.exception))

    def test_disable_persists_in_settings(self):
        registry = ModuleRegistry()
        non_core = next((m.id for m in registry.manifests.values() if not m.core), None)
        if non_core is None:
            self.skipTest("no non-core module installed")
        self.disable(non_core)
        store = Store(self.data_dir)
        self.assertIs(store.get_setting(f"module.{non_core}.enabled"), False)
        # And the modules command reflects it.
        code, out = self.run_cli("modules", "list", "--json")
        self.assertEqual(code, EXIT_OK)
        rows = json.loads(out)
        row = next(r for r in rows if r["id"] == non_core)
        self.assertFalse(row["enabled"])


if __name__ == "__main__":
    unittest.main()
