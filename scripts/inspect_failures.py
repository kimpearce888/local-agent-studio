#!/usr/bin/env python3
"""Inspect denials and handoff failure messages of the latest validate dir."""
import glob
import json
import sqlite3
import sys

D = sys.argv[1]
db = sqlite3.connect(f"{D}/studio.db")
print("--- denied tools ---")
for (p,) in db.execute("select payload from events where type='tool.denied' order by seq"):
    pl = json.loads(p)
    print(pl.get("task_id"), pl.get("tool"), pl.get("rule"))
print("--- failure messages per run ---")
for run_dir in sorted(glob.glob(f"{D}/runs/run-*")):
    for att in sorted(glob.glob(f"{run_dir}/attempts/*/att*/messages.jsonl")):
        for line in open(att, encoding="utf-8"):
            m = json.loads(line)
            c = m.get("content") or ""
            if "failed validation" in c:
                print(run_dir[-14:], att.split("/")[-2],
                      f"[{m.get('role')}]", c[:200].replace("\n", " | "))
