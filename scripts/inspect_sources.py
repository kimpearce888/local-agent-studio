#!/usr/bin/env python3
"""Show the key_facts the model wrote (sources) and how tool results
are rendered in the conversation."""
import glob
import json
import re
import sys

D = sys.argv[1]
print("--- key_facts in final replies ---")
for run in sorted(glob.glob(f"{D}/runs/run-*")):
    for final in sorted(glob.glob(f"{run}/attempts/*/att*/final.txt")):
        text = open(final, encoding="utf-8").read()
        m = re.search(r'"key_facts":\s*(\[.*?\])\s*,\s*\n', text, re.DOTALL)
        snippet = (m.group(1)[:260] if m else "no key_facts found")
        print(run[-12:], final.split("/")[-2], snippet.replace("\n", " ")[:260])
print("--- tool result rendering (first run, first attempt) ---")
first = sorted(glob.glob(f"{D}/runs/run-*/attempts/*/att*/messages.jsonl"))
if first:
    for line in open(first[0], encoding="utf-8"):
        m = json.loads(line)
        role = m.get("role")
        c = m.get("content") or ""
        if role == "tool" or c.startswith("TOOL") or "result" in c[:60].lower():
            print(f"[{role}]", c[:170].replace("\n", " | "))
