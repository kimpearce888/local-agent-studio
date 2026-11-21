"""Internationalization (PRD section 11): Unicode end to end.

A multilingual corpus - CJK, right-to-left Arabic, emoji, accented
Latin, and mixed scripts inside one goal - must survive the whole
pipeline byte-exactly: goal text, constraint extraction, task titles,
handoffs, events, and both export formats.
"""

import json
import tempfile
import unittest

from las.modules.execution import DriverConfig, Engine
from las.modules.planning import Planner
from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.modules.reporting import export_markdown, export_json_bundle

CJK_GOAL = ("整理季度销售数据并撰写中文摘要。所有数字必须注明来源。"
            "绝不编造数据。")
CJK_CONSTRAINTS = ["所有数字必须注明来源。", "绝不编造数据。"]
ARABIC_GOAL = ("اكتب ملخصًا موجزًا عن نتائج الربع. يجب ذكر المصدر لكل رقم. "
               "لا تختلق أرقامًا.")
EMOJI_GOAL = ("Résumé du marché 🚲 - analyse des vélos électriques. "
              "Chaque chiffre doit être sourcé. Jamais de chiffres inventés. "
              "旅行计划：京都三天行程 ✈️")


def reply_for(goal_text: str, constraints: list, title: str) -> dict:
    return {
        "title": title,
        "constraints": [{"id": f"c{i + 1}", "text": c, "authority": "user"}
                        for i, c in enumerate(constraints)],
        "tasks": [
            {"id": "t1", "type": "task", "title": title,
             "instructions": f"第一步：完成「{title}」的全部工作并核对来源。"
                             " تحقق من كل مصدر بعناية.", "needs": []},
            {"id": "t2", "type": "collector",
             "title": f"{title} - 汇总 ✅",
             "instructions": "汇总第一步的所有结果，写成最终摘要并再次核对。"
                             " اكتب الملخص النهائي الآن.",
             "needs": ["t1"]},
        ],
    }


def handoff_text_unicode(task_id: str, plan) -> str:
    obj = {
        "task_id": task_id, "status": "done",
        "summary": f"完成 {task_id} ✅ الملخص جاهز",
        "key_facts": [{"fact": "市场增长 20% 📈", "source": "message:1"}],
        "binding_constraints": [
            {"id": c.id, "text": c.text, "state": "active",
             "prerequisite": c.prerequisite, "authority": c.authority,
             "fallback": c.fallback}
            for c in plan.constraints],
        "decisions": [], "open_issues": [], "deliverables": [],
    }
    return (f"完成 {task_id} ✅\n<handoff>\n"
            + json.dumps(obj, ensure_ascii=False, indent=1) + "\n</handoff>")


def drive(goal_text, constraints, title, data_dir):
    adapter = MockAdapter([{"json": reply_for(goal_text, constraints, title)}])
    result = Planner(adapter, "mock-model").plan(goal_text)
    return result


class TestUnicodeEndToEnd(unittest.TestCase):
    def _run_goal(self, goal_text, constraints, title):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = drive(goal_text, constraints, title, tmp.name)
        self.assertTrue(result.valid, result.issues)
        plan = result.plan
        # Constraints extracted verbatim from the goal text (AC-03).
        self.assertEqual([c.text for c in plan.constraints], constraints)
        engine = Engine(tmp.name)
        run_id = engine.create_run(plan)
        script = [{"text": handoff_text_unicode(t.id, plan)}
                  for t in plan.tasks]
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter(script))
        engine.driver(run_id, registry, {"default": ("mock", "mock-model")},
                      DriverConfig(failure_policy="stop")).run()
        state = engine.state(run_id)
        self.assertEqual(state.status, "succeeded")
        return engine, run_id, state, plan

    def test_cjk_goal_survives_pipeline_and_exports(self):
        engine, run_id, state, plan = self._run_goal(
            CJK_GOAL, CJK_CONSTRAINTS, "季度销售摘要")
        self.assertEqual(state.title, "季度销售摘要")
        # Constraint carry-over is byte-exact in every handoff.
        for ts in state.tasks.values():
            carried = [c["text"] for c in ts.handoff["binding_constraints"]]
            self.assertEqual(carried, CJK_CONSTRAINTS)
        md = export_markdown(state, engine.store)
        self.assertIn("季度销售摘要", md)
        self.assertIn("所有数字必须注明来源。", md)
        bundle = export_json_bundle(state, engine.store)
        self.assertEqual(bundle["run"]["title"], "季度销售摘要")
        dumped = json.dumps(bundle, ensure_ascii=False)
        self.assertIn("📈", dumped)                 # emoji survive export

    def test_arabic_rtl_goal_survives_pipeline(self):
        constraints = ["يجب ذكر المصدر لكل رقم.", "لا تختلق أرقامًا."]
        engine, run_id, state, plan = self._run_goal(
            ARABIC_GOAL, constraints, "ملخص الربع")
        self.assertEqual(state.title, "ملخص الربع")
        for ts in state.tasks.values():
            carried = [c["text"] for c in ts.handoff["binding_constraints"]]
            self.assertEqual(carried, constraints)

    def test_mixed_scripts_and_emoji_in_one_goal(self):
        constraints = ["Chaque chiffre doit être sourcé.",
                       "Jamais de chiffres inventés."]
        engine, run_id, state, plan = self._run_goal(
            EMOJI_GOAL, constraints, "Résumé 🚲")
        self.assertEqual(state.title, "Résumé 🚲")
        md = export_markdown(state, engine.store)
        self.assertIn("Résumé 🚲", md)
        self.assertIn("✈️", md)                     # composed emoji pair


if __name__ == "__main__":
    unittest.main()
