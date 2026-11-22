#!/usr/bin/env python3
"""Print the last N messages of every attempt in a validate dir."""
import glob
import json
import sys

D = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3
for run in sorted(glob.glob(f"{D}/runs/run-*")):
    print(f"=== {run[-16:]}")
    for att in sorted(glob.glob(f"{run}/attempts/*/att*/messages.jsonl")):
        lines = [json.loads(x) for x in open(att, encoding="utf-8")]
        print(f"  -- {att.split('/')[-3]}/{att.split('/')[-2]} ({len(lines)} msgs)")
        for m in lines[-N:]:
            role = m.get("role")
            c = (m.get("content") or "").replace("\n", " | ")
            print(f"    [{role}] {c[:230]}")
