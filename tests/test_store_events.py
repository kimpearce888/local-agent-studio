"""Event log: hash chain, tamper detection, storage (spec 6.3-6.5)."""

import sqlite3
import unittest

from las.modules.storage import DBNewerError, Store, ProviderProfile, canonical_json


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class TestEventLog(StoreTestBase):
    def test_append_assigns_sequences(self):
        e1 = self.store.append("r1", "run.created", {"plan_id": "p1"})
        e2 = self.store.append("r1", "task.ready", {"task_id": "t1"})
        self.assertEqual((e1.seq, e2.seq), (1, 2))
        self.assertEqual(e1.prev_hash, "0" * 64)
        self.assertEqual(e2.prev_hash, e1.hash)

    def test_chain_verifies(self):
        for i in range(10):
            self.store.append("r1", "task.ready", {"task_id": f"t{i}"})
        self.assertTrue(self.store.verify("r1"))

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(ValueError):
            self.store.append("r1", "task.exploded", {})

    def test_events_after_seq(self):
        for i in range(5):
            self.store.append("r1", "task.ready", {"task_id": f"t{i}"})
        tail = self.store.events("r1", after=3)
        self.assertEqual([e.seq for e in tail], [4, 5])

    def test_tampered_payload_detected(self):
        self.store.append("r1", "task.ready", {"task_id": "t1"})
        self.store.append("r1", "task.succeeded", {"task_id": "t1"})
        # Tamper directly in SQLite.
        self.store.db.execute(
            "UPDATE events SET payload=? WHERE run_id='r1' AND seq=2",
            (canonical_json({"task_id": "t1", "code": "ok"}),))
        self.store.db.commit()
        self.assertFalse(self.store.verify("r1"))

    def test_run_ids_order(self):
        self.store.append("r1", "run.created", {})
        self.store.append("r2", "run.created", {})
        self.assertEqual(self.store.run_ids(), ["r2", "r1"])


class TestStoreData(StoreTestBase):
    def test_settings_defaults(self):
        self.assertEqual(self.store.get_setting("runs.failure_policy"), "ask")
        self.assertEqual(self.store.get_setting("runs.cap_tokens"), 2_000_000)
        self.assertEqual(self.store.get_setting("export.ai_notice"), True)
        self.assertIsNone(self.store.get_setting("nope"))
        self.store.set_setting("runs.cap_tokens", 500)
        self.assertEqual(self.store.get_setting("runs.cap_tokens"), 500)

    def test_provider_roundtrip(self):
        profile = ProviderProfile(id="lm", name="LM Studio", kind="lmstudio",
                                  base_url="http://localhost:1234",
                                  auth_env="env:LM_KEY", wire="chat")
        self.store.save_provider(profile)
        loaded = self.store.get_provider("lm")
        self.assertEqual(loaded.to_obj(), profile.to_obj())
        self.store.delete_provider("lm")
        self.assertIsNone(self.store.get_provider("lm"))

    def test_plan_roundtrip(self):
        from tests.planlib import make_plan
        plan = make_plan()
        self.store.save_plan(plan.to_obj())
        obj = self.store.get_plan(plan.id)
        self.assertEqual(obj["goal"], plan.goal)
        self.assertEqual(len(obj["tasks"]), 3)

    def test_wal_mode(self):
        mode = self.store.db.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_newer_db_refused(self):
        self.store.close()
        db = sqlite3.connect(self.store.db_path)
        db.execute("UPDATE meta SET value='2' WHERE key='db_schema_version'")
        db.commit()
        db.close()
        with self.assertRaises(DBNewerError):
            Store(self.tmp.name)


if __name__ == "__main__":
    unittest.main()
