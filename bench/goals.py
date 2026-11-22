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


# -- workspace fixtures -------------------------------------------------------
#
# The corpus goals describe work on USER MATERIAL ("last quarter's sales",
# "the parser module", "the data-retention policy").  A benchmark that
# asks for sourced facts but seeds an empty workspace measures
# hallucination pressure, not task completion - every serious agent
# benchmark provisions its inputs (GAIA ships attachments, SWE-bench
# ships the repo).  Each family therefore gets deterministic input
# files, written into the run workspace before the run starts, so the
# measured loop is the product's real one: read the workspace, extract
# file-sourced facts, write deliverables.  All data is synthetic and
# labelled as such; the model cites file paths (H-06), never invents.

FIXTURES = {
    "market-brief": [
        ("sources/market-notes-2026.md",
         "# E-bike market notes (sample data, clipped by the user)\n"
         "- Global e-bike sales 2025: 42.5 million units (notes p.1).\n"
         "- Average selling price 2025: 1,540 USD (notes p.1).\n"
         "- Europe share of sales: 31 percent (notes p.2).\n"
         "- Mid-drive motor share of new models: 58 percent (notes p.2).\n"
         "- Battery capacity trend: 480 Wh to 630 Wh average 2023-2025 (p.3).\n"
         "- Reported city-fleet orders up 18 percent YoY (notes p.3).\n"),
    ],
    "csv-summary": [
        ("sales-q3-2026.csv",
         "region,month,revenue_usd,units\n"
         "north,2026-07,412500,310\n"
         "north,2026-08,398200,295\n"
         "north,2026-09,441000,330\n"
         "south,2026-07,251400,190\n"
         "south,2026-08,262800,198\n"
         "south,2026-09,288100,215\n"
         "east,2026-07,305000,240\n"
         "east,2026-08,318600,248\n"
         "east,2026-09,334200,260\n"
         "west,2026-07,199800,150\n"
         "west,2026-08,210500,158\n"
         "west,2026-09,224700,169\n"),
    ],
    "research-digest": [
        ("sources/heatpump-excerpts.md",
         "# Heat pump excerpts (sample data, three sources)\n"
         "Source A (installer brochure 2025): COP 4.2 at 7 C outside; "
         "installation 2-3 days; 62 percent of surveyed homes cut gas use.\n"
         "Source B (energy agency table 2025): average retrofit cost "
         "9,800 USD; grant covers up to 30 percent in 2026.\n"
         "Source C (trade article 2026): cold-climate models hold COP "
         "2.8 at -15 C; noise levels 38-52 dB.\n"),
    ],
    "file-organization": [
        ("downloads/invoice-2026-04.txt",
         "Invoice 2026-04 (sample data) - hardware order, total 640 USD.\n"),
        ("downloads/invoice-2026-05.txt",
         "Invoice 2026-05 (sample data) - software renewal, total 120 USD.\n"),
        ("downloads/report-draft-q2.md",
         "# Q2 report draft (sample data)\nRevenue up 6 percent; churn flat.\n"),
        ("downloads/photo-notes.txt",
         "Photo notes (sample data): 214 raw files from the Kyoto trip.\n"),
        ("downloads/recipes-raw.txt",
         "Recipe scratchpad (sample data): miso soup, weeknight pasta.\n"),
    ],
    "trip-itinerary": [
        ("kyoto-trip-notes.md",
         "# Kyoto trip notes (from the user)\n"
         "- Three days in April; budget 600 USD total excluding hotel.\n"
         "- Must include: Fushimi Inari, one museum, a tea ceremony.\n"
         "- No more than 40 minutes of transit between stops.\n"
         "- One vegetarian dinner required.\n"),
    ],
    "meeting-notes": [
        ("meeting-2026-11-10.txt",
         "Raw meeting notes 2026-11-10 (sample data)\n"
         "- Attendance: Dana, Wei, Priya, Marc\n"
         "- Release 0.2.0: slipped one week, now due the 24th\n"
         "- Onboarding docs: Priya owns, draft by Friday\n"
         "- Budget: travel budget trimmed 10 percent next quarter\n"
         "- Action items: Wei sends status template; Marc books review\n"),
    ],
    "budget-review": [
        ("team-budget-2026.csv",
         "line,q1_planned,q1_actual,q2_planned,q2_actual\n"
         "travel,8000,9200,8000,6100\n"
         "software,4500,4500,4800,5100\n"
         "training,3000,1800,3000,3200\n"
         "events,6000,7500,6000,5900\n"
         "hardware,9000,9400,9000,8800\n"),
    ],
    "comparison-matrix": [
        ("laptop-specs.md",
         "# Laptop shortlist (sample data, vendor spec sheets)\n"
         "- AeroBook 14: 14in, 1.1 kg, 18 h battery, 16 GB, 999 USD\n"
         "- WorkStation 15: 15.6in, 1.9 kg, 11 h battery, 32 GB, 1499 USD\n"
         "- TravelLite 13: 13.3in, 0.95 kg, 14 h battery, 16 GB, 1199 USD\n"
         "- StudioPro 16: 16in, 2.1 kg, 12 h battery, 64 GB, 2399 USD\n"),
    ],
    "newsletter": [
        ("drafts/section-release.md",
         "## Release news (draft, sample data)\nVersion 0.2.0 lands the "
         "24th: faster search, new exports.\n"),
        ("drafts/section-team.md",
         "## Team news (draft, sample data)\nPriya joined docs; Marc "
         "moves to review duty in December.\n"),
        ("drafts/section-tips.md",
         "## Tips (draft, sample data)\nThree ways to cut setup time in "
         "half with presets.\n"),
    ],
    "literature-review": [
        ("sources/paper-2024-sleep.md",
         "Paper 2024-A (abstract, sample data): n=48, sleep spindles "
         "correlated 0.41 with recall; effect largest in deep sleep.\n"),
        ("sources/paper-2025-consolidation.md",
         "Paper 2025-B (abstract, sample data): 90-min naps improved "
         "consolidation 12 percent; replication in two cohorts.\n"),
        ("sources/paper-2026-deprivation.md",
         "Paper 2026-C (abstract, sample data): restricted sleep (5 h) "
         "cut recall 18 percent across 3 nights; recovery incomplete.\n"),
    ],
    "code-review": [
        ("parser.py",
         "import re\n\n"
         "DEFAULT_STOPS = []\n\n\n"
         "def tokenize(text, stops=DEFAULT_STOPS):\n"
         "    words = text.split()\n"
         "    return [w for w in words if w not in stops]\n\n\n"
         "def parse_kv(line):\n"
         "    try:\n"
         "        key, value = line.split(':', 1)\n"
         "        return {key.strip(): value.strip()}\n"
         "    except Exception:\n"
         "        return {}\n\n\n"
         "def top_terms(text, n):\n"
         "    counts = {}\n"
         "    for w in tokenize(text):\n"
         "        counts[w] = counts.get(w, 0) + 1\n"
         "    items = sorted(counts.items(), key=lambda kv: kv[1])\n"
         "    return items[-n:]\n"),
    ],
    "refactor-plan": [
        ("storage_layer.py",
         "import json, os\n\n\n"
         "def save_json(path, obj):\n"
         "    with open(path, 'w') as fh:\n"
         "        json.dump(obj, fh)\n\n\n"
         "def save_json_pretty(path, obj):\n"
         "    with open(path, 'w') as fh:\n"
         "        json.dump(obj, fh, indent=2)\n\n\n"
         "def load_json(path):\n"
         "    with open(path) as fh:\n"
         "        return json.load(fh)\n\n\n"
         "def load_json_safe(path):\n"
         "    try:\n"
         "        with open(path) as fh:\n"
         "            return json.load(fh)\n"
         "    except (OSError, ValueError):\n"
         "        return None\n"),
    ],
    "data-cleanup": [
        ("customers.csv",
         "id,name,email,city\n"
         "1,ana lopez,Ana.Lopez@Example.com,Madrid\n"
         "2,Ana Lopez,ana.lopez@example.com,madrid\n"
         "3,wei chen,wei.chen@corp.cn,Beijing\n"
         "4,Wei Chen,WEI.CHEN@corp.cn,beijing\n"
         "5,marc duval,marc.duval@mail.fr,Lyon\n"
         "6,priya nair,priya.nair@web.in,Chennai\n"
         "7,Priya Nair,priya.nair@web.in,chennai\n"),
    ],
    "inventory-report": [
        ("inventory-notes.txt",
         "Home inventory scratchpad (sample data)\n"
         "Living room: sofa (800 USD), TV 55in (600 USD), 40 books (5 USD each)\n"
         "Kitchen: fridge (900 USD), oven (300 USD), table set (250 USD)\n"
         "Office: laptop (1400 USD), monitor (350 USD), chair (220 USD)\n"
         "Bedroom: bed (1100 USD), wardrobe (400 USD)\n"),
    ],
    "recipe-collection": [
        ("recipes-raw.txt",
         "Recipe scratchpad (sample data)\n"
         "- miso soup: dashi, miso paste, tofu, 15 min\n"
         "- weeknight pasta: garlic, chili, olive oil, parmesan, 20 min\n"
         "- sheet-pan chicken: thighs, lemon, potatoes, 45 min\n"
         "- lentil salad: lentils, cucumber, feta, 25 min\n"),
    ],
    "study-guide": [
        ("networking-syllabus.md",
         "# Networking exam syllabus (sample data)\n"
         "1. OSI and TCP/IP layers\n"
         "2. IP addressing and subnetting (CIDR)\n"
         "3. TCP vs UDP, handshake, windows\n"
         "4. DNS resolution and records\n"
         "5. HTTP methods, status codes, TLS basics\n"
         "6. NAT, firewalls, common ports\n"),
    ],
    "product-spec": [
        ("user-feedback.md",
         "# User feedback quotes (sample data)\n"
         "- \"I lose track of what I read\" (11 of 14 interviews)\n"
         "- \"I want page numbers, not just titles\" (8 of 14)\n"
         "- \"Sync can wait; a reading streak motivates me\" (6 of 14)\n"
         "- \"Export my notes as CSV\" (5 of 14)\n"),
    ],
    "release-notes": [
        ("commits-0.2.0.txt",
         "Commits since 0.1.0 (sample data)\n"
         "a1b2c3 feat: search index 3x faster\n"
         "d4e5f6 fix: export crash on empty shelf\n"
         "78a9b0 feat: CSV export\n"
         "c1d2e3 fix: import duplicate detection\n"
         "e4f5a6 docs: new setup guide\n"
         "Breaking: bookmarks.json moves to data/bookmarks.json\n"),
    ],
    "survey-analysis": [
        ("survey-responses.csv",
         "respondent,remote_preference,focus_days,meetings_ok\n"
         "1,fully_remote,3,2\n"
         "2,hybrid,4,3\n"
         "3,fully_remote,5,1\n"
         "4,office,2,4\n"
         "5,hybrid,3,3\n"
         "6,fully_remote,5,2\n"
         "7,hybrid,4,3\n"
         "8,fully_remote,4,1\n"),
    ],
    "policy-summary": [
        ("retention-policy.md",
         "# Data retention policy (sample data, internal)\n"
         "1. Customer records: kept 5 years after last interaction.\n"
         "2. Support tickets: kept 2 years, then anonymized.\n"
         "3. Backups: rotated every 90 days.\n"
         "4. Deletion requests: honoured within 30 days.\n"
         "5. Analytics events: aggregated after 12 months.\n"
         "6. Exceptions require written approval from the DPO.\n"),
    ],
}


def seed_workspace(workspace, goal: BenchGoal) -> list:
    """Write the goal family's input files into ``workspace`` (the run's
    jailed folder).  Returns the paths written.  Deterministic: the same
    goal always seeds the same bytes, so real-model runs are comparable
    across machines and over time."""
    from las.modules.security import Workspace as _W  # noqa: F401  (type hint)
    written = []
    for rel_path, content in FIXTURES.get(goal.family, ()):
        workspace.write_file(rel_path, content)
        written.append(rel_path)
    return written
