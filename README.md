# Local Agent Studio

Turn one large prompt into a chain of small tasks, and run each task as its own
isolated agent session — one after another — on models running entirely on
your machine.

A planner splits the goal into tasks with **binding constraints**. You review
and edit the plan. A scheduler runs every task in a **fresh session** with its
own context, passes results forward through a **structured, validated
handoff**, and lets collector tasks combine earlier results before the next
stage starts. Nothing leaves the machine except requests to the model servers
you configure.

This repository contains the **engine and CLI** (milestone M0 of the plan in
[docs/PLAN.md](docs/PLAN.md)). The desktop shell (Tauri) arrives in M2; the
engine is deliberately shell-agnostic.

## Highlights

- **Strict isolation and order** — every task is a new session; exactly one
  session is active per run (RUN-2). A canary planted in task 1 never reaches
  task 3 unless a dependency carries it.
- **Constraints travel intact** — binding constraints move between tasks as
  structured fields copied *verbatim*, checked by SHA-256 at every handoff
  (H-03). Compaction can never touch them. A dropped or reworded constraint
  fails validation.
- **Durable, event-sourced runs** — every state change is a hash-chained event
  in SQLite (WAL), written before the next action. Crash or kill the process:
  the run resumes from the last completed task (RUN-5).
- **Least agency** — only workspace file tools are on by default; shell and
  web fetch are off until granted, allowlisted, and audited. The Rule of Two
  is enforced: a task that reads untrusted input, reaches private systems and
  changes external state needs an approval gate (L-01).
- **Hard budgets by default** — per-task and per-run caps on tokens, wall
  time, turns and tool calls (RUN-8).
- **Zero dependencies** — the engine is Python 3.10+ standard library only.

## Quickstart

```bash
# no install needed
python -m las --help

# or install the `studio` command
pip install .

# offline smoke test with the deterministic mock provider
studio selftest

# connect a local model server (LM Studio, Ollama, llama.cpp, vLLM, Unsloth…)
studio providers detect
studio providers add --name lmstudio --preset lmstudio
studio providers test lmstudio

# plan and run
studio plan "Compare three note-taking apps for a privacy-conscious lawyer. \
You must cite every claim. Never recommend cloud-only tools." -o plan.json
studio run plan.json --provider lmstudio
```

### Try it right now, with no server

```bash
python -m las selftest            # three-task chain against the mock provider
python -m las list                # the self-test run appears in history
python -m las inspect <run_id>    # task states, attempts, handoffs
```

## CLI reference (spec 7.4)

| Command | Purpose |
| --- | --- |
| `studio plan "GOAL" -o plan.json` | Generate a plan; `--granularity`, `--max-tasks`, `--provider`, `--model` |
| `studio run PLAN` | Run a plan or template; `--goal`, `--var k=v`, `--on-failure stop\|skip\|ask`, `--cap-tokens`, `--pause-after`, `--force` |
| `studio list` / `studio inspect RUN` | History and detail; `--task ID`, `--json` |
| `studio resume RUN` | Resume a paused or failed run |
| `studio approve RUN TASK --reason "…"` / `studio reject` | Gate decisions |
| `studio retry / skip / rerun RUN TASK` | Failure-panel actions (S8) |
| `studio export RUN --format markdown\|json -o PATH` | Report with provenance + AI notice |
| `studio providers detect / add / test / list` | Manage providers, run the capability probe |
| `studio selftest` | Built-in three-task plan, hard-capped (DST-2) |
| `studio verify RUN` | Verify a run's event hash chain |

Exit codes: `0` success · `1` run failed · `2` invalid plan or arguments ·
`3` provider unreachable/auth · `4` waiting at a gate · `5` cap reached ·
`6` cancelled · `7` internal error.

## Supported servers

Presets for **LM Studio** (`localhost:1234`), **Ollama** (`:11434`),
**llama.cpp server** (`:8080`), **vLLM** (`:8000`), **Unsloth Studio** (Beta,
ports vary) and any generic OpenAI-compatible endpoint. Two wire adapters —
Chat Completions and Responses (Open Responses compatible) — sit behind one
internal message model; the engine always sends full history and never relies
on server-side tools. For Unsloth, `enable_tools: false` is sent on every
request (PRV-5).

API keys are referenced as `env:VARNAME` (never stored in plans, events or
exports); OS keychain integration lands with the desktop shell in M2.

## Architecture

```
las/
├── schema/    plan schema v1 + validation (V-01..V-12),
│              handoff schema + validation (H-01..H-08)
├── store.py   SQLite WAL: hash-chained event log, providers, plans, settings
├── providers/ adapter contract, Chat Completions + Responses adapters,
│              presets, capability probe, deterministic mock
├── workspace.py  jailed per-run file area (symlink-safe, size-limited)
├── tools.py   tool registry, permission broker, Rule-of-Two flags
├── context.py context assembly (blocks 1-7), token budget, compaction ladder
├── planner.py planner contract, JSON-schema output, repair loop
├── linter.py  lint rules L-01..L-09
├── runstate.py  task/run state machines, event-log replay
├── scheduler.py sequential driver: gates, failures, caps, resume, rerun
├── export.py  Markdown report + JSON bundle with provenance
├── selftest.py  built-in three-task self-test
└── cli.py     the `studio` command
```

Full requirements and design: [docs/prd.md](docs/prd.md) (PRD v2.0) and
[docs/product-spec.md](docs/product-spec.md) (specification v1.0). Build plan
and Phase-0 decisions: [docs/PLAN.md](docs/PLAN.md).

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the acceptance scenarios that need no GUI or live server:
plan repair (AC-02), constraint extraction (AC-03), session isolation (AC-04),
constraint preservation (AC-05), crash resume (AC-06), budget caps (AC-07),
Rule of Two (AC-08), provider tool policy (AC-09), compaction (AC-10) and
rerun-from-here (AC-13), plus the validation rules, the workspace jail and
the wire adapters against a local SSE mock server.

## Security notes

- Least agency by default; every tool call is broker-checked and audited.
- Workspace paths are jailed with resolved symlinks and size limits.
- The event log is hash-chained and verified on load; a tampered run is
  refused.
- Provider keys live in environment references, never in plans, logs or
  exports.
- Untrusted content (tool output, fetched pages) is labelled and fenced.

## License

MIT — see [LICENSE](LICENSE). Each model you run carries its own licence,
which you must follow.
