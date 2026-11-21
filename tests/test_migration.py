"""Maintainability (PRD section 11): forward-compatible data migrations.

v0.1.0 stores predate the run_summary cache (added in 0.2.0 behind
CREATE TABLE IF NOT EXISTS - no SCHEMA_VERSION bump, so no dump and
restore).  Opening a legacy directory must transparently recreate the
cache and list old runs correctly.
"""

import sqlite3
import tempfile
import unittest

from las.modules.execution import Engine
from las.modules.storage import Store


def legacy_run(tmp: str, title: str = "Legacy run"):
    """A v0.1.0-shaped run: current store, then run_summary dropped."""
    # Build a minimal run through the engine, then age the schema.
    from las.shared.plan_contract import Plan, Task
    plan = Plan(id="plan-legacy", title=title, goal=title,
                created_at="2026-11-01T00:00:00Z", tasks=[
                    Task(id="t1", type="collector", title="Only task",
                         instructions="Do the legacy thing completely.",
                         needs=[])])
    engine = Engine(tmp)
    run_id = engine.create_run(plan, title=title)
    engine.store.append(run_id, "run.succeeded", {})
    engine.store.close()
    db = sqlite3.connect(_db_path(tmp))
    db.execute("DROP TABLE IF EXISTS run_summary")   # 0.1.0 had no cache
    db.commit()
    db.close()
    return run_id


def _db_path(tmp: str) -> str:
    import os
    return os.path.join(tmp, "studio.db")


class TestLegacyDataDir(unittest.TestCase):
    def test_v010_dir_opens_and_rebuilds_the_summary_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = legacy_run(tmp)
            with sqlite3.connect(_db_path(tmp)) as db:
                names = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("run_summary", names)   # legacy confirmed
            engine = Engine(tmp)                        # current code opens it
            rows = engine.list_summaries()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], run_id)
            self.assertEqual(rows[0]["title"], "Legacy run")
            self.assertEqual(rows[0]["status"], "succeeded")
            with sqlite3.connect(_db_path(tmp)) as db:
                names = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("run_summary", names)     # cache recreated
            state = engine.state(run_id)                # replay still works
            self.assertEqual(state.status, "succeeded")

    def test_legacy_history_lists_after_new_runs(self):
        """Old and new runs coexist in one listing."""
        with tempfile.TemporaryDirectory() as tmp:
            legacy_run(tmp, "Legacy run")
            from las.shared.plan_contract import Plan, Task
            plan = Plan(id="plan-new", title="New run", goal="New run",
                        created_at="2026-11-21T00:00:00Z", tasks=[
                            Task(id="t1", type="collector", title="Task",
                                 instructions="Do the new thing completely.",
                                 needs=[])])
            engine = Engine(tmp)
            engine.store.append(engine.create_run(plan, title="New run"),
                                "run.succeeded", {})
            titles = sorted(r["title"] for r in engine.list_summaries())
            self.assertEqual(titles, ["Legacy run", "New run"])


if __name__ == "__main__":
    unittest.main()
