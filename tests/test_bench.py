"""The benchmark package: corpus determinism, metric math, fault injection,
cap enforcement and the network audit - at suite-friendly scale."""

import tempfile
import unittest

from bench.goals import build_corpus
from bench.harness import (
    cap_check, kill_cycles, run_corpus, socket_audit,
)
from las.modules.providers.mock import MockAdapter  # functional dep: the bench scenarios run on the mock provider


class TestCorpus(unittest.TestCase):
    def test_one_hundred_deterministic_goals(self):
        first, second = build_corpus(), build_corpus()
        self.assertEqual(len(first), 100)
        self.assertEqual(first, second)                # byte-identical corpus
        self.assertEqual(len({g.gid for g in first}), 100)
        kinds = [g.kind for g in first]
        self.assertEqual((kinds.count("clean"),
                          kinds.count("repair"),
                          kinds.count("gated")), (88, 8, 4))
        self.assertTrue(all(g.text and g.title and g.task_titles
                            and g.constraints for g in first))


class TestCorpusMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.goals = build_corpus(10)

    def tearDown(self):
        self.tmp.cleanup()

    def test_metrics_match_the_scripted_outcomes(self):
        metrics = run_corpus(self.tmp.name, self.goals)
        kinds = [g.kind for g in self.goals]
        self.assertEqual(metrics["goals"], 10)
        self.assertEqual(metrics["valid_after_repair"],
                         kinds.count("clean") + kinds.count("repair")
                         + kinds.count("gated"))
        self.assertEqual(metrics["first_attempt_valid"],
                         kinds.count("clean") + kinds.count("gated"))
        self.assertEqual(metrics["gate_pauses"], kinds.count("gated"))
        self.assertEqual(metrics["completed_without_help"], 10 - kinds.count("gated"))
        self.assertEqual(metrics["plan_failures"], 0)
        self.assertEqual(metrics["run_failures"], 0)
        self.assertEqual(sum(metrics["repair_rounds"]),
                         kinds.count("repair"))


class TestFaultInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.goals = [g for g in build_corpus(10) if g.kind != "gated"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_kill_resumes_cleanly(self):
        result = kill_cycles(self.tmp.name, cycles=6, seed=7,
                             goals=self.goals)
        self.assertEqual(result["cycles"], 6)
        self.assertEqual(result["resumed_ok"], 6)
        self.assertEqual(result["failures"], [])


class TestCapEnforcement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cap_is_terminal_and_no_work_continues(self):
        result = cap_check(self.tmp.name)
        self.assertTrue(result["cap_reached"])
        self.assertEqual(result["work_after_cap"], 0)
        self.assertEqual(result["status"], "failed")


class TestNetworkAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_zero_outbound_connections_by_default(self):
        result = socket_audit(self.tmp.name)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["outbound_attempts"], [])


if __name__ == "__main__":
    unittest.main()
