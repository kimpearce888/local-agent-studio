#!/usr/bin/env python3
"""Extract a partial-results report from an interrupted real-model bench.

The bench writes its report JSON only at the end; when a session budget
ends the run first, this recovers the measured sample from the event
store + the harness log so the numbers are still recorded honestly.
"""

import glob
import json
import sqlite3
import sys
from datetime import datetime, timezone

data_dir, log_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

statuses = []
db = sqlite3.connect(f"{data_dir}/studio.db")
for run_id, ev_type in db.execute(
        "select run_id, type from events where type in "
        "('run.succeeded','run.failed','run.blocked','run.paused') order by seq"):
    statuses.append({"run": run_id, "outcome": ev_type.removeprefix("run.")})

goal_lines = []
for line in open(log_path, encoding="utf-8"):
    if " goal " in line and "(" in line:
        goal_lines.append(line.strip())

completed = sum(1 for s in statuses if s["outcome"] == "succeeded")
blocked = sum(1 for s in statuses if s["outcome"] == "blocked")
failed = sum(1 for s in statuses if s["outcome"] == "failed")

report = {
    "provider": {"id": "zai", "model": "glm-4-plus",
                 "note": "PARTIAL sample - session budget ended the run "
                         "before the corpus/kills finished; per-goal "
                         "outcomes recovered from the event store"},
    "recovered_at": datetime.now(timezone.utc).isoformat(),
    "measured_goals": len(statuses),
    "outcomes": {"completed_without_help": completed,
                 "blocked_honest": blocked, "failed": failed},
    "per_run": statuses,
    "harness_log_tail": goal_lines[-12:],
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2)
print(json.dumps(report["outcomes"]))
print(f"measured {len(statuses)} goals -> {out_path}")
