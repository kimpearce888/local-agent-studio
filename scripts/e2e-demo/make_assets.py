#!/usr/bin/env python3
"""Build the end-to-end demo assets: the sample input, the mock
provider's planning reply and its run script.

The demo workflow: a small sales CSV is attached to a goal; the
program plans two tasks (compute totals, then write summary.md behind
an approval gate) and solves them offline with the deterministic mock
provider.  All numbers in the mock script are the REAL totals of the
sample CSV, so the run's output can be verified against the input.
"""

import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# -- the input -----------------------------------------------------------

ROWS = [
    ("Widget A", 120, 9.50),
    ("Widget B", 85, 12.25),
    ("Widget C", 210, 4.10),
    ("Widget D", 60, 21.00),
]

csv_path = os.path.join(HERE, "sales.csv")
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["product", "units", "unit_price", "revenue"])
    for name, units, price in ROWS:
        w.writerow([name, units, f"{price:.2f}", f"{units * price:.2f}"])

# The REAL totals, computed here so the scripted model tells the truth.
total_units = sum(r[1] for r in ROWS)
total_revenue = sum(r[1] * r[2] for r in ROWS)
top = max(ROWS, key=lambda r: r[1])
top_line = f"{top[0]} ({top[1]} units)"
assert (total_units, round(total_revenue, 2)) == (475, 4302.25), \
    (total_units, total_revenue)

GOAL = (
    "Read the attached sales.csv, compute the total units sold and the "
    "total revenue, and write summary.md with both totals and the "
    "best-selling product. You must cite sales.csv for every figure. "
    "Never invent numbers. Writing the public summary requires approval.")

CONSTRAINTS = [
    {"id": "c1", "text": "You must cite sales.csv for every figure.",
     "authority": "user", "scope": "all"},
    {"id": "c2", "text": "Never invent numbers.",
     "authority": "user", "scope": "all"},
    {"id": "c3", "text": "Writing the public summary requires approval.",
     "authority": "user", "scope": "all"},
]


def handoff(task_id, summary, deliverables):
    return (
        f"Work finished for {task_id}. {summary}\n"
        "<handoff>\n"
        + json.dumps({
            "task_id": task_id,
            "status": "done",
            "summary": summary,
            "key_facts": [
                {"fact": f"Total units sold: {total_units}",
                 "source": "file:sales.csv"},
                {"fact": f"Total revenue: {total_revenue:.2f}",
                 "source": "file:sales.csv"},
                {"fact": f"Best-selling product by units: {top_line}",
                 "source": "file:sales.csv"},
            ],
            "binding_constraints": [
                {"id": c["id"], "text": c["text"], "state": "active",
                 "authority": "user"} for c in CONSTRAINTS],
            "decisions": [],
            "open_issues": [],
            "deliverables": deliverables,
        }, ensure_ascii=False, indent=1)
        + "\n</handoff>")


# -- the mock provider's planning reply ----------------------------------

plan_reply = {
    "title": "Sales CSV summary",
    "constraints": CONSTRAINTS,
    "tasks": [
        {
            "id": "t1", "type": "task",
            "title": "Compute the sales totals from sales.csv",
            "instructions": (
                "Read sales.csv from the workspace. Compute the total "
                "units sold, the total revenue and the best-selling "
                "product by units. Write the three results to "
                "totals.json."),
            "needs": [],
            "expected_output": "totals.json with total_units, "
                               "total_revenue and top_product",
            "constraints": ["c1", "c2"],
            "role": "worker",
            "tools": ["read_file", "write_file"],
            "gate_before": False,
        },
        {
            "id": "t2", "type": "task",
            "title": "Write summary.md (approval required)",
            "instructions": (
                "Using t1's results, write summary.md with the total "
                "units, the total revenue and the best-selling "
                "product. Cite sales.csv for every figure."),
            "needs": ["t1"],
            "expected_output": "summary.md with the verified totals",
            "constraints": ["c1", "c2", "c3"],
            "role": "worker",
            "tools": ["read_file", "write_file"],
            "gate_before": True,
        },
    ],
}

with open(os.path.join(HERE, "plan-script.json"), "w", encoding="utf-8") as fh:
    json.dump([{"json": plan_reply}], fh, indent=1)

# -- the mock provider's run script (one turn per model call) -------------

totals_json = json.dumps({
    "total_units": total_units,
    "total_revenue": total_revenue,
    "top_product": top_line,
}, indent=1)

summary_md = (
    "# Sales summary\n\n"
    f"Total units sold: **{total_units}** (sales.csv).\n\n"
    f"Total revenue: **{total_revenue:.2f}** (sales.csv).\n\n"
    f"Best-selling product by units: **{top[0]}** with "
    f"{top[1]} units (sales.csv).\n")

run_turns = [
    # t1 turn 1: read the input
    {"tool_calls": [{"name": "read_file",
                     "arguments": {"path": "sales.csv"}}]},
    # t1 turn 2: write the computed totals
    {"tool_calls": [{"name": "write_file",
                     "arguments": {"path": "totals.json",
                                   "content": totals_json}}]},
    # t1 turn 3: the handoff ends the task
    {"text": handoff("t1", "Computed the sales totals from sales.csv "
                    "and wrote totals.json.",
                    [{"path": "totals.json"}])},
    # t2 turn 1 (after the gate is approved): read t1's deliverable
    {"tool_calls": [{"name": "read_file",
                     "arguments": {"path": "totals.json"}}]},
    # t2 turn 2: write the summary
    {"tool_calls": [{"name": "write_file",
                     "arguments": {"path": "summary.md",
                                   "content": summary_md}}]},
    # t2 turn 3: the handoff ends the run
    {"text": handoff("t2", "Wrote summary.md with the verified totals.",
                    [{"path": "summary.md"}])},
]

with open(os.path.join(HERE, "run-script.json"), "w", encoding="utf-8") as fh:
    json.dump(run_turns, fh, indent=1)

with open(os.path.join(HERE, "goal.txt"), "w", encoding="utf-8") as fh:
    fh.write(GOAL + "\n")

print("assets written to", HERE)
print("  sales.csv      -", len(ROWS), "products, totals",
      total_units, "units /", f"{total_revenue:.2f}")
print("  plan-script.json - mock planner reply (plan JSON)")
print("  run-script.json  - 6 mock turns (2 tools + handoff x 2 tasks)")
print("  goal.txt         - the user's goal text")
