"""M1 optimization: derived run summaries - `list` replays a run only
when its cached summary is stale; the cache always matches a replay."""

import tempfile
import unittest

from las.modules.execution import Engine
from las.modules.storage import Store
from tests.planlib import make_plan
from tests.util import run_with_mock, success_turn


class SummaryCacheBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self.engine, run_id, _, _ = run_with_mock(
            make_plan(),
            [success_turn(make_plan(), t.id) for t in make_plan().tasks],
            self.data_dir)
        self.run_id = run_id

    def tearDown(self):
        self.tmp.cleanup()


class TestSummaryCache(SummaryCacheBase):
    def test_summary_matches_replay(self):
        rows = self.engine.list_summaries()
        self.assertEqual(len(rows), 1)
        state = self.engine.state(self.run_id)
        row = rows[0]
        self.assertEqual(row["status"], state.status)
        self.assertEqual(row["tasks_total"], len(state.plan.tasks))
        self.assertEqual(row["tasks_done"],
                         sum(1 for t in state.tasks.values()
                             if t.state == "succeeded"))
        self.assertEqual(row["tokens"], state.tokens_used())

    def test_second_listing_does_not_replay(self):
        self.engine.list_summaries()
        calls = {"n": 0}
        original = self.engine.state

        def counting(run_id):
            calls["n"] += 1
            return original(run_id)

        self.engine.state = counting
        rows = self.engine.list_summaries()
        self.assertEqual(calls["n"], 0)      # everything came from the cache
        self.assertEqual(len(rows), 1)

    def test_new_events_refresh_the_cache(self):
        self.engine.list_summaries()
        # A new event makes the cached summary stale by seq.
        Store(self.data_dir).append(self.run_id, "run.paused",
                                    {"reason": "test"})
        rows = self.engine.list_summaries()
        self.assertEqual(rows[0]["status"], "paused")

    def test_delete_run_drops_the_summary_row(self):
        self.engine.list_summaries()
        Store(self.data_dir).delete_run(self.run_id)
        self.assertIsNone(Store(self.data_dir).get_summary(self.run_id))
        self.assertEqual(self.engine.list_summaries(), [])

    def test_status_filter(self):
        rows = self.engine.list_summaries(status="succeeded")
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.engine.list_summaries(status="failed"), [])


if __name__ == "__main__":
    unittest.main()
