"""Library: reusable templates and role profiles (LIB-1, LIB-2, spec S10).

Templates are validated plan snapshots with a name, description, task
count and variables. Built-in templates ship with the app, cannot be
deleted and can be duplicated. Role profiles capture a role's system
prompt, parameters, tool allowlist and default model.
"""

from __future__ import annotations

from typing import Optional

from las.modules.planquality import validate_plan
from las.modules.storage import Store
from las.shared.plan_contract import Plan, plan_variables
from las.shared.tool_catalog import KNOWN_TOOLS

ROLES = ("planner", "worker", "collector")


class LibraryError(ValueError):
    """An actionable library error (message is user-facing)."""


def _builtin_plan(goal: str, tasks: list[dict], constraints: list[dict],
                  title: str) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema_version": 1, "id": "builtin", "title": title, "goal": goal,
            "created_at": now, "options": {"granularity": "normal",
                                           "max_tasks": 30,
                                           "stage_barriers": False},
            "constraints": constraints, "tasks": tasks}


BRIEFING_TASKS = [
    {"id": "gather", "title": "Gather facts",
     "instructions": "Gather five sourced facts about the topic and write "
                     "them to facts.md with a source for each.",
     "needs": [], "expected_output": "facts.md", "constraints": ["c1", "c2"],
     "role": "worker"},
    {"id": "draft", "title": "Draft the brief",
     "instructions": "Draft the deliverable from the gathered facts and "
                     "write it to draft.md.",
     "needs": ["gather"], "expected_output": "draft.md",
     "constraints": ["c1", "c2"], "role": "worker"},
    {"id": "collect", "title": "Collect the final result",
     "instructions": "Combine the earlier outputs into the final result at "
                     "result.md and check every constraint.",
     "needs": ["draft"], "expected_output": "result.md",
     "constraints": ["c1", "c2"], "role": "collector"},
]

BUILTIN_CONSTRAINTS = [
    {"id": "c1", "text": "You must cite every figure."},
    {"id": "c2", "text": "Never invent numbers."},
]


def _tasks(*specs) -> list[dict]:
    return [{"id": tid, "title": title, "instructions": instructions,
             "needs": needs, "expected_output": out, "constraints": ["c1", "c2"],
             "role": role}
            for tid, title, instructions, needs, out, role in specs]


def builtin_templates() -> list[dict]:
    """The four built-ins named in LIB-1: research report, document analysis,
    code review, content pipeline. All carry variables for reuse."""
    return [
        {"id": "builtin-research-report", "name": "Research report",
         "description": "Gather sourced facts, draft, collect a cited report about {{topic}}.",
         "builtin": True,
         "plan": _builtin_plan(
             "Produce a short, sourced research report about {{topic}}.",
             _tasks(
                 ("gather", "Gather facts",
                  "Gather five sourced facts about {{topic}} and write them "
                  "to facts.md with a source for each.", [], "facts.md", "worker"),
                 ("draft", "Draft the report",
                  "Draft the report from the gathered facts and write it to "
                  "draft.md with an executive summary.", ["gather"],
                  "draft.md", "worker"),
                 ("collect", "Collect the final report",
                  "Combine the draft into the final report at result.md and "
                  "check every citation.", ["draft"], "result.md", "collector")),
             BUILTIN_CONSTRAINTS, "Research report")},
        {"id": "builtin-document-analysis", "name": "Document analysis",
         "description": "Extract claims and figures from a document, assess them, summarize.",
         "builtin": True,
         "plan": _builtin_plan(
             "Analyze the attached document and summarize its key claims.",
             _tasks(
                 ("extract", "Extract claims",
                  "Read the document in the workspace and extract its key "
                  "claims with page or section references into claims.md.",
                  [], "claims.md", "worker"),
                 ("assess", "Assess the claims",
                  "Assess each claim: supported, unsupported or "
                  "contradicted, with the evidence, and write assessments.md.",
                  ["extract"], "assessments.md", "worker"),
                 ("collect", "Summarize",
                  "Write the final analysis summary to result.md covering "
                  "every assessed claim.", ["assess"], "result.md",
                  "collector")),
             BUILTIN_CONSTRAINTS, "Document analysis")},
        {"id": "builtin-code-review", "name": "Code review",
         "description": "Review a change set for correctness, security and tests.",
         "builtin": True,
         "plan": _builtin_plan(
             "Review the code in the workspace and report the findings.",
             _tasks(
                 ("survey", "Survey the change",
                  "List the changed files and what each change does; write "
                  "survey.md.", [], "survey.md", "worker"),
                 ("review", "Review for defects",
                  "Review the changes for correctness problems, security "
                  "issues and missing tests; write findings.md with a "
                  "severity for each finding.", ["survey"], "findings.md",
                  "worker"),
                 ("collect", "Collect the review",
                  "Write the final review to result.md with must-fix and "
                  "nice-to-have sections.", ["review"], "result.md",
                  "collector")),
             BUILTIN_CONSTRAINTS, "Code review")},
        {"id": "builtin-content-pipeline", "name": "Content pipeline",
         "description": "Outline, draft and polish a piece of content about {{topic}}.",
         "builtin": True,
         "plan": _builtin_plan(
             "Produce a polished piece of content about {{topic}}.",
             _tasks(
                 ("outline", "Outline",
                  "Write an outline for the content about {{topic}} to "
                  "outline.md with section goals.", [], "outline.md", "worker"),
                 ("draft", "Draft",
                  "Write the full draft following the outline to draft.md.",
                  ["outline"], "draft.md", "worker"),
                 ("polish", "Polish",
                  "Polish the draft for tone, length and clarity; write "
                  "final.md.", ["draft"], "final.md", "worker"),
                 ("collect", "Collect",
                  "Write the final content to result.md and verify it "
                  "follows the outline.", ["polish"], "result.md",
                  "collector")),
             BUILTIN_CONSTRAINTS, "Content pipeline")},
    ]


def ensure_builtin_templates(store: Store) -> None:
    """Seed built-in templates on first use (idempotent, never overwrites
    user edits of duplicates)."""
    known = {t["id"] for t in store.list_templates()}
    for t in builtin_templates():
        if t["id"] not in known:
            store.save_template(template_entry(t["plan"], t["name"],
                                               t["description"], t["id"],
                                               builtin=True))


def template_entry(plan_obj: dict, name: str, description: str,
                   template_id: str = "", builtin: bool = False) -> dict:
    """Validate a plan and build the stored template object."""
    plan = Plan.from_obj(plan_obj)
    issues = validate_plan(plan)
    if any(i.rule != "V-12" for i in issues):     # V-12: variables are the point
        raise LibraryError(
            "The plan does not validate: " + "; ".join(str(i) for i in issues))
    template_id = template_id or ("tpl-" + plan.id)
    return {"id": template_id, "name": name, "description": description,
            "builtin": builtin, "task_count": len(plan.tasks),
            "variables": plan_variables(plan), "plan": plan.to_obj()}


def save_template(store: Store, plan_obj: dict, name: str,
                  description: str = "", template_id: str = "",
                  builtin: bool = False) -> dict:
    entry = template_entry(plan_obj, name, description, template_id, builtin)
    store.save_template(entry)
    return entry


def delete_template(store: Store, template_id: str) -> None:
    for t in store.list_templates():
        if t["id"] == template_id:
            if t.get("builtin"):
                raise LibraryError(
                    f"Template {template_id} is built in and cannot be deleted; "
                    f"duplicate it instead.")
            store.save_template  # keep linters honest; deletion below
            store.db.execute("DELETE FROM templates WHERE id=?", (template_id,))
            store.db.commit()
            return
    raise LibraryError(f"No template named {template_id}.")


def duplicate_template(store: Store, template_id: str, new_name: str = "") -> dict:
    source = next((t for t in store.list_templates() if t["id"] == template_id), None)
    if source is None:
        raise LibraryError(f"No template named {template_id}.")
    entry = dict(source)
    entry["id"] = template_id + "-copy-" + str(len(store.list_templates()))
    entry["name"] = new_name or (source["name"] + " (copy)")
    entry["builtin"] = False
    entry["plan"] = dict(source["plan"])
    entry["plan"]["id"] = entry["id"]
    store.save_template(entry)
    return entry


def save_role_profile(store: Store, profile: dict) -> dict:
    """Validate and store a role profile (LIB-2)."""
    for key in ("id", "name", "role"):
        if not str(profile.get(key, "")):
            raise LibraryError(f"A role profile needs {key}.")
    if profile["role"] not in ROLES:
        raise LibraryError(f"Role must be one of {', '.join(ROLES)}.")
    tools = profile.get("tools") or []
    for tool in tools:
        if tool not in KNOWN_TOOLS:
            raise LibraryError(f"Unknown tool {tool}.")
    entry = {
        "id": profile["id"], "name": profile["name"], "role": profile["role"],
        "system_prompt": str(profile.get("system_prompt", "")),
        "params": dict(profile.get("params") or {}),
        "tools": list(tools),
        "default_model": dict(profile.get("default_model") or {}),
    }
    store.save_role_profile(entry)
    return entry


def get_role_profile(store: Store, profile_id: str) -> Optional[dict]:
    return store.get_role_profile(profile_id)


BRIEFING_TASKS = _tasks(
    ("gather", "Gather facts",
     "Gather five sourced facts about the topic and write them to facts.md "
     "with a source for each.", [], "facts.md", "worker"),
    ("draft", "Draft the brief",
     "Draft the deliverable from the gathered facts and write it to "
     "draft.md.", ["gather"], "draft.md", "worker"),
    ("collect", "Collect the final result",
     "Combine the earlier outputs into the final result at result.md and "
     "check every constraint.", ["draft"], "result.md", "collector"))


__all__ = [
    "BUILTIN_CONSTRAINTS", "BRIEFING_TASKS", "LibraryError", "ROLES",
    "builtin_templates", "delete_template", "duplicate_template",
    "ensure_builtin_templates", "get_role_profile", "save_role_profile",
    "save_template", "template_entry",
]
