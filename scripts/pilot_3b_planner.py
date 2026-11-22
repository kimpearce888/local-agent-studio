#!/usr/bin/env python3
"""Planner-only pilot: qwen2.5:3b against the corpus, no worker runs.

The full live bench executes every valid plan as a worker run, which
on CPU-bound 3b takes 10-30 min per goal.  The question the 3b pilot
exists to answer is the PLANNER one: after the wire schema started
declaring the role/type enums (v0.4.1), do 3b plans validate?  This
script measures exactly that - first-attempt validity, validity after
repair, and the validation issues of every failure - without running
a single worker task.

Usage: PYTHONPATH=. python3 scripts/pilot_3b_planner.py [n_goals]
"""

import json
import sys
import time
from collections import Counter

from bench.goals import build_corpus
from bench.harness import real_adapter
from las.modules.planning import Planner
from las.modules.providers import probe

MODEL = "qwen2.5:3b"
BASE = "http://127.0.0.1:11434"


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    goals = build_corpus(n)
    adapter = real_adapter("ollama", BASE)
    caps = probe(adapter, MODEL, "ollama")
    print(f"probe: json_schema={caps.json_schema} streaming={caps.streaming} "
          f"tools={caps.tool_calling}")

    rows = []
    t0 = time.monotonic()
    jsonl = open(f"bench/results/pilot-planner-"
                 f"{MODEL.replace(':', '-')}.jsonl", "w",
                 encoding="utf-8")
    for i, goal in enumerate(goals):
        planner = Planner(adapter, MODEL,
                          params={"temperature": 0.2, "max_tokens": 4096},
                          json_schema_supported=caps.json_schema)
        r = planner.plan(goal.text)
        row = {
            "goal": goal.title, "kind": goal.kind, "valid": r.valid,
            "repair_rounds": r.repair_rounds,
            "issues": [str(x) for x in (r.issues or [])][:5]}
        rows.append(row)
        jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
        jsonl.flush()
        print(f"  [{i + 1}/{len(goals)}] {goal.kind:6s} "
              f"{goal.title[:40]:40s} "
              f"valid={r.valid} repairs={r.repair_rounds} "
              f"({time.monotonic() - t0:.0f}s)", flush=True)
        for iss in row["issues"][:3]:
            print(f"      issue: {iss[:150]}", flush=True)
    jsonl.close()

    valid = sum(1 for r in rows if r["valid"])
    first = sum(1 for r in rows if r["valid"] and r["repair_rounds"] == 0)
    issue_counts = Counter(
        i.split("(")[0].strip() for r in rows if not r["valid"]
        for i in r["issues"])
    report = {
        "model": MODEL, "provider": "ollama", "goals": len(rows),
        "valid_after_repair": valid, "first_attempt_valid": first,
        "json_schema_used": caps.json_schema,
        "failure_issue_rules": dict(issue_counts), "rows": rows,
    }
    out = f"bench/results/pilot-planner-{MODEL.replace(':', '-')}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"\n{MODEL}: {first}/{len(rows)} first attempt, "
          f"{valid}/{len(rows)} after repair")
    print(f"failure rules: {dict(issue_counts) or '(none)'}")
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
