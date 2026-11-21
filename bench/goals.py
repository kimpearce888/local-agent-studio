"""The Phase 0 benchmark corpus (PRD section 17): 100 deterministic goals.

Twenty scenario families drawn from the product requirements and the user
guide, five variants each. Every goal carries the goal text with its
binding constraints and the scripted behavior class used by the mock
dry-run:

- ``clean``  the scripted model replies with a valid plan on the first
  attempt (88 goals);
- ``repair`` the first reply breaks a plan rule (a dependency cycle) and
  the second is valid, exercising the planner repair loop (8 goals);
- ``gated``  the plan is valid but one task requires approval, so an
  unattended run pauses at the gate (4 goals - the PRD's canonical
  approval scenario).

With a real provider the scripts are ignored: the corpus is then just
goals, and the same harness measures the model against the PRD targets.
The mock numbers validate the MACHINERY, not any model.
"""

from dataclasses import dataclass

# Constraint sentences embedded in goal texts and repeated by the scripted
# planner (constraint extraction, AC-03, must find them).
CITE = "You must cite every figure."
NEVER_INVENT = "Never invent numbers."
APPROVAL = "The result requires approval before publishing."
NO_EXTERNAL = "Do not use external services."

CONSTRAINT_ROTATION = [
    (CITE, NEVER_INVENT),
    (NEVER_INVENT, NO_EXTERNAL),
    (CITE, NO_EXTERNAL),
    (NEVER_INVENT, APPROVAL),
    (CITE, NEVER_INVENT, NO_EXTERNAL),
]

# (family, title, task titles).  Task sets of 3-6, realistic handoffs.
TEMPLATES = [
    ("market-brief", "E-bike market brief", [
        "Gather five sourced facts", "Draft the brief", "Collect the final brief"]),
    ("csv-summary", "Quarterly sales summary", [
        "Load the sales CSV", "Compute per-region totals", "Write the summary",
        "Collect the report"]),
    ("research-digest", "Research digest on heat pumps", [
        "Collect three sources", "Extract claims with citations", "Draft the digest",
        "Collect the digest"]),
    ("file-organization", "Downloads folder reorganization", [
        "Inventory the folder", "Propose a category scheme", "Move files by scheme",
        "Verify nothing was lost", "Write the change log"]),
    ("trip-itinerary", "Three-day Kyoto itinerary", [
        "List the constraints", "Draft day-by-day plans", "Check travel times",
        "Collect the itinerary"]),
    ("meeting-notes", "Meeting notes to minutes", [
        "Read the raw notes", "Group by topic", "Draft the minutes",
        "Collect the minutes"]),
    ("budget-review", "Team budget review", [
        "Load the budget sheet", "Flag overruns", "Draft recommendations",
        "Collect the review"]),
    ("comparison-matrix", "Laptop comparison matrix", [
        "Fix the criteria", "Fill the matrix from specs", "Summarize trade-offs",
        "Collect the matrix"]),
    ("newsletter", "Team newsletter issue", [
        "Gather section drafts", "Edit for length", "Assemble the issue",
        "Collect the newsletter"]),
    ("literature-review", "Literature review on sleep and memory", [
        "Collect papers", "Summarize each paper", "Synthesize findings",
        "Collect the review"]),
    ("code-review", "Code review of the parser module", [
        "Read the module", "List issues by severity", "Draft the review",
        "Collect the review"]),
    ("refactor-plan", "Refactor plan for the storage layer", [
        "Map current structure", "Propose target structure", "Sequence the steps",
        "Collect the plan"]),
    ("data-cleanup", "Customer data cleanup", [
        "Profile the dataset", "Define dedupe rules", "Apply and verify",
        "Collect the cleanup report"]),
    ("inventory-report", "Home inventory report", [
        "Walk each room", "Record items", "Estimate values", "Collect the report"]),
    ("recipe-collection", "Weeknight recipe collection", [
        "Shortlist recipes", "Standardize the format", "Build the shopping list",
        "Collect the collection"]),
    ("study-guide", "Study guide for the networking exam", [
        "List exam topics", "Summarize each topic", "Write practice questions",
        "Collect the guide"]),
    ("product-spec", "Product spec for a reading tracker", [
        "State the problem", "Define requirements", "Sketch the interface",
        "Collect the spec"]),
    ("release-notes", "Release notes for 0.2.0", [
        "List merged changes", "Group by impact", "Draft upgrade notes",
        "Collect the notes"]),
    ("survey-analysis", "Survey analysis on remote work", [
        "Load responses", "Compute distributions", "Draft findings",
        "Collect the analysis"]),
    ("policy-summary", "Policy summary on data retention", [
        "Read the policy", "Extract obligations", "Draft the summary",
        "Collect the summary"]),
]

GOAL_SUBJECT = {
    "market-brief": "the e-bike market",
    "csv-summary": "last quarter's sales",
    "research-digest": "heat pumps",
    "file-organization": "my downloads folder",
    "trip-itinerary": "a three-day trip to Kyoto",
    "meeting-notes": "the last team meeting",
    "budget-review": "the team budget",
    "comparison-matrix": "current laptops",
    "newsletter": "this month's team newsletter",
    "literature-review": "sleep and memory",
    "code-review": "the parser module",
    "refactor-plan": "the storage layer",
    "data-cleanup": "the customer dataset",
    "inventory-report": "my home inventory",
    "recipe-collection": "weeknight dinners",
    "study-guide": "the networking exam",
    "product-spec": "a reading tracker",
    "release-notes": "version 0.2.0",
    "survey-analysis": "the remote-work survey",
    "policy-summary": "the data-retention policy",
}

# Deterministic script assignment over the 100 goals.
GATED_TEMPLATES = {0}                     # 4 gated goals (variants 0-3)
REPAIR_SLOTS = {(2, 0), (5, 0), (8, 0), (11, 0), (14, 0), (17, 0), (3, 2), (7, 2)}


@dataclass(frozen=True)
class BenchGoal:
    gid: str                    # g001 .. g100
    text: str                   # goal with binding constraints
    family: str
    title: str
    task_titles: tuple
    constraints: tuple          # sentences the planner must repeat
    kind: str                   # clean | repair | gated


def build_corpus(total: int = 100) -> list:
    """Deterministic: the same 100 goals on every call and every machine."""
    if total > len(TEMPLATES) * 5:
        raise ValueError("corpus holds 100 goals")
    goals = []
    for i in range(total):
        t, v = divmod(i, 5)
        family, title, tasks = TEMPLATES[t]
        kind = "clean"
        if t in GATED_TEMPLATES and v < 4:
            kind = "gated"
        elif (t, v) in REPAIR_SLOTS:
            kind = "repair"
        constraints = list(CONSTRAINT_ROTATION[v % len(CONSTRAINT_ROTATION)])
        if kind == "gated" and APPROVAL not in constraints:
            # A gated plan only makes sense with an approval constraint.
            constraints.append(APPROVAL)
        text = (f"{title} about {GOAL_SUBJECT[family]}. "
                + " ".join(constraints))
        goals.append(BenchGoal(
            gid=f"g{i + 1:03d}", text=text, family=family, title=title,
            task_titles=tuple(tasks), constraints=tuple(constraints), kind=kind))
    return goals
