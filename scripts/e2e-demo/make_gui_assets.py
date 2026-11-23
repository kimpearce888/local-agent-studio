#!/usr/bin/env python3
"""Build the GUI-demo variant of the workflow: a plan that needs no
input files (the GUI goal screen has no attachment upload yet), so the
whole create-and-solve loop can run inside the desktop app offline.

t1 drafts a two-line announcement (writes draft.txt); t2 publishes
announcement.md behind an approval gate.  Same workspace, so t2 reads
what t1 wrote.  Every sentence in the scripted draft is under 12 words
and mentions no unreleased features - the plan's constraints hold.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

GOAL = (
    "Draft a two-line product announcement for Widget C, then publish it "
    "as announcement.md. You must keep every sentence under 12 words. "
    "Never mention unreleased features. Publishing the announcement "
    "requires approval.")

CONSTRAINTS = [
    {"id": "c1", "text": "You must keep every sentence under 12 words.",
     "authority": "user", "scope": "all"},
    {"id": "c2", "text": "Never mention unreleased features.",
     "authority": "user", "scope": "all"},
    {"id": "c3", "text": "Publishing the announcement requires approval.",
     "authority": "user", "scope": "all"},
]

DRAFT = ("Widget C is here.\n"
         "Four hundred twenty sold in week one.\n"
         "Compact, affordable, and shipping today.\n")

ANNOUNCEMENT = ("# Announcing Widget C\n\n"
                "Widget C is here.\n"
                "Four hundred twenty sold in week one.\n"
                "Compact, affordable, and shipping today.\n")


def handoff(task_id, summary, facts, deliverables):
    return (
        f"Work finished for {task_id}. {summary}\n"
        "<handoff>\n"
        + json.dumps({
            "task_id": task_id,
            "status": "done",
            "summary": summary,
            "key_facts": [{"fact": f, "source": s} for f, s in facts],
            "binding_constraints": [
                {"id": c["id"], "text": c["text"], "state": "active",
                 "authority": "user"} for c in CONSTRAINTS],
            "decisions": [],
            "open_issues": [],
            "deliverables": [{"path": p} for p in deliverables],
        }, ensure_ascii=False, indent=1)
        + "\n</handoff>")


plan_reply = {
    "title": "Widget C announcement",
    "constraints": CONSTRAINTS,
    "tasks": [
        {
            "id": "t1", "type": "task",
            "title": "Draft the two-line announcement",
            "instructions": (
                "Draft a two-line product announcement for Widget C. "
                "Keep every sentence under 12 words. Write the draft to "
                "draft.txt."),
            "needs": [],
            "expected_output": "draft.txt with the two-line announcement",
            "constraints": ["c1", "c2"],
            "role": "worker",
            "tools": ["write_file", "read_file"],
            "gate_before": False,
        },
        {
            "id": "t2", "type": "task",
            "title": "Publish announcement.md (approval required)",
            "instructions": (
                "Read the draft produced by t1 and publish it as "
                "announcement.md, keeping every sentence under 12 words."),
            "needs": ["t1"],
            "expected_output": "announcement.md, published",
            "constraints": ["c1", "c2", "c3"],
            "role": "worker",
            "tools": ["read_file", "write_file"],
            "gate_before": True,
        },
    ],
}

with open(os.path.join(HERE, "gui-plan-script.json"), "w", encoding="utf-8") as fh:
    json.dump([{"json": plan_reply}], fh, indent=1)

run_turns = [
    # t1: write the draft, then hand off
    {"tool_calls": [{"name": "write_file",
                     "arguments": {"path": "draft.txt", "content": DRAFT}}]},
    {"text": handoff("t1", "Drafted the two-line Widget C announcement "
                    "and wrote draft.txt.",
                    [("The draft has two lines", "draft.txt"),
                     ("Every sentence is under 12 words", "draft.txt")],
                    ["draft.txt"])},
    # t2 (after gate approval): read the draft, publish, hand off
    {"tool_calls": [{"name": "read_file",
                     "arguments": {"path": "draft.txt"}}]},
    {"tool_calls": [{"name": "write_file",
                     "arguments": {"path": "announcement.md",
                                   "content": ANNOUNCEMENT}}]},
    {"text": handoff("t2", "Published announcement.md from the approved "
                    "draft.",
                    [("announcement.md contains the approved draft",
                      "announcement.md")],
                    ["announcement.md"])},
]

with open(os.path.join(HERE, "gui-run-script.json"), "w", encoding="utf-8") as fh:
    json.dump(run_turns, fh, indent=1)

with open(os.path.join(HERE, "gui-goal.txt"), "w", encoding="utf-8") as fh:
    fh.write(GOAL + "\n")

print("GUI assets written:", ", ".join(sorted(
    f for f in os.listdir(HERE) if f.startswith("gui-"))))
