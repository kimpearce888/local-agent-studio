#!/usr/bin/env python3
"""Re-validate the LAST handoff of each failed attempt: shows exactly
which rules killed it (the repair message never got sent - the task
failed terminal instead)."""
import glob
import json
import sqlite3
import sys

sys.path.insert(0, "/home/z/my-project/local-agent-studio")
from las.modules.handoffs import Handoff, extract_handoff, validate_handoff  # noqa: E402
from las.shared.plan_contract import Plan  # noqa: E402

D = sys.argv[1]
db = sqlite3.connect(f"{D}/studio.db")
plan_by_run = {}
for (p,) in db.execute("select payload from events where type='run.created'"):
    pl = json.loads(p)
    if "plan" in pl:
        plan_by_run[pl.get("run_id") or ""] = None

for run_dir in sorted(glob.glob(f"{D}/runs/run-*")):
    run_id = run_dir.rsplit("/", 1)[-1]
    first = None
    for ev_json in db.execute(
            "select payload from events where run_id=? and type='run.created'",
            (run_id,)):
        pl = json.loads(ev_json[0])
        if "plan" in pl:
            first = pl
    if first is None:
        continue
    plan = Plan.from_obj(first["plan"])
    task = plan.tasks[0]
    for att in sorted(glob.glob(f"{run_dir}/attempts/{task.id}/att*/messages.jsonl")):
        lines = [json.loads(x) for x in open(att, encoding="utf-8")]
        last = None
        for m in lines:
            if m.get("role") == "assistant":
                last = m.get("content") or ""
        if not last:
            continue
        handoff = extract_handoff(last)
        if handoff is None:
            print(run_id[-8:], task.id, "final reply has NO handoff")
            continue
        issues = validate_handoff(handoff, task, plan)
        if issues:
            print(run_id[-8:], task.id, "LAST handoff issues:",
                  [str(i)[:110] for i in issues])
        else:
            print(run_id[-8:], task.id, "last handoff VALID")
