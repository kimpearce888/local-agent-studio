# Local Agent Studio — Engineering Plan

Version 0.1 · November 21, 2026 · Owner: engineering · Status: active

Source documents: [PRD v2.0](prd.md) and [Product Specification v1.0](product-spec.md).
Where this plan and those documents disagree, the PRD wins. Requirement IDs (PRV-*, PLN-*, RUN-*, …) refer to the PRD; section numbers like 5.4 refer to the specification.

## 1. Objective

Build Local Agent Studio to 1.0 along the PRD's six phases, starting with the engine core.
The first milestone (M0, this repo's initial drop) delivers a working, tested engine and CLI
that can plan, run, inspect, resume and export a chained-task run entirely offline against a
deterministic mock provider, so every core invariant is provable in CI before a real model
server is ever attached.

## 2. Phase-0 decisions

The PRD leaves open questions OQ-2 … OQ-10. These are decided now; each can be revisited at
the gate it affects, with a decision record in `docs/adr/`.

| OQ | Decision | Rationale |
| --- | --- | --- |
| OQ-2 Engine language | **Python 3.10+, standard library only** (sqlite3, urllib, json, dataclasses) | Zero dependencies keeps the install-and-run promise, gives us SQLite and an HTTP client for free, and lets the fault-injection and erosion suites run anywhere. No pip resolution, no supply-chain surface (ASI04). |
| OQ-3 Shell | **Tauri, confirmed in Phase 2** after the canvas accessibility test (per PRD 14). Electron stays the documented fallback. | Engine-first build order makes the shell swappable: the engine exposes the loopback API from spec 7.5 and the shell is a client. |
| OQ-4 Licence / distribution | **MIT, open source** for v0 | Keeps the non-commercial open-source lane under the Cyber Resilience Act available (PRD §13, COM-1); the publisher can move to commercial distribution later, at which point COM-1/COM-2 become mandatory. |
| OQ-5 Hardware / reference model | Defer to the M1 benchmark on a 16 GB RAM, CPU-only machine; engine already tracks tokens/second for estimates | Nothing in M0 depends on it. |
| OQ-6 Plan structure | **Dependency graph whose display order equals one deterministic execution order** (the spec's proposal) | Matches the sequential scheduler (RUN-2) and keeps "rerun from here" well defined (spec 5.10). |
| OQ-7 Platform priority | Windows 11, macOS (current + 2), Ubuntu 22.04+; decided with the signing arrangements in M4 | Installer pipeline is exercised from Phase 1 (PRD §18) but signing keys are an M4 procurement. |
| OQ-8 EU distribution | Assume **yes** for design purposes | Compliance controls (COM-1..COM-7) are built in from M0 even though they only bind on commercial distribution. |
| OQ-9 Crash reports | Off; opt-in design only (P1) | Matches SET-3. |
| OQ-10 Model licences | Read from server metadata when present; repository lookup is P1 | The model picker surface arrives with the UI in M2. |
| Keys (spec 6.4) | v0 stores provider keys only as `env:NAME` references or in the local profiles DB (never in plans, events or exports); OS keychain integration lands with the shell in M2 | Spec 6.4 already defines this fallback for headless sessions. |

## 3. Architecture

One engine, one process, event-sourced. The engine is a library; the CLI (`studio`) and the
future loopback HTTP API (spec 7.5) are both thin drivers over it.

```
┌─────────────┐   ┌──────────────────────────────────────────────────────┐
│ CLI (M0) /   │   │ Engine                                                │
│ Tauri (M2) / │──▶│  Planner ─▶ Plan v1 ─▶ Linter ─▶ Scheduler           │
│ HTTP API (M1)│   │                         │                            │
└─────────────┘   │            RunDriver (one task attempt at a time)    │
                   │                │            │                        │
                   │   Context asm │ Permission broker + Rule of Two     │
                   │   + budgets   │ Workspace jail (tools)               │
                   │                ▼                                     │
                   │  Provider adapters (Chat Completions / Responses)    │
                   │  ── mock (M0) ── LM Studio / Ollama / llama.cpp /    │
                   │     vLLM / Unsloth / generic (M1)                    │
                   └──────────────┬───────────────────────────────────────┘
                                  ▼
              SQLite (WAL): hash-chained event log = source of truth
              runs/<id>/: workspace/, attempts/, handoffs/, export/
```

### Module map (M0)

| Module | Responsibility | Spec |
| --- | --- | --- |
| `las/schema/plan.py` | Plan schema v1, task/constraint models, validation V-01…V-12 | 6.1, 5.2 |
| `las/schema/handoff.py` | Handoff schema, `<handoff>` extraction, validation H-01…H-08 (SHA-256 verbatim constraint check) | 6.2, 5.5 |
| `las/store.py` | SQLite WAL store: events (hash-chained), providers, plans, templates, settings | 6.3, 6.4, 6.5 |
| `las/providers/base.py` | Internal message model, adapter contract, normalized provider errors | 7.1 |
| `las/providers/openai_compat.py` | Chat Completions + Responses wire adapters, SSE streaming, capability-aware | 7.1, 7.2 |
| `las/providers/presets.py` | LM Studio, Unsloth, Ollama, llama.cpp, vLLM, generic presets; `enable_tools:false` policy | 7.2, PRV-5 |
| `las/providers/probe.py` | Capability probe (≤6 requests, ≤2,000 tokens, 24 h cache) | 7.3 |
| `las/providers/mock.py` | Deterministic scripted mock adapter (tests, demo, self-test) | PRD §19 |
| `las/workspace.py` | Per-run workspace jail, file tools, size limits, manifest | TLS-1 |
| `las/tools.py` | Tool registry, permission broker, tool execution, Rule-of-Two flags | TLS-2, 5.9 |
| `las/context.py` | Context assembly (blocks 1–7), token budget, compaction ladder | 5.4, RUN-1, RUN-9 |
| `las/planner.py` | Planner contract, JSON-schema output, lenient parse, repair loop, critique pass | 5.1, PLN-1..5 |
| `las/linter.py` | Lint rules L-01…L-09 | 5.3, BLD-7 |
| `las/runstate.py` | Task/run state machines, replay of the event log | 5.6, 6.3 |
| `las/scheduler.py` | RunDriver: sequential execution, gates, failures (5.7 table), caps, controls, rerun | 5.6, 5.7, 5.8, 5.10 |
| `las/export.py` | Markdown report + JSON bundle with provenance and AI notice | OBS-4, COM-4 |
| `las/selftest.py` | Built-in three-task plan, hard-capped (DST-2 self-test) | S1 step 5 |
| `las/messages.py` | Message catalogue (code → message + fix) | 8 |
| `las/cli.py` | `studio` CLI: plan, run, list, inspect, resume, approve/reject, retry/skip/rerun, export, providers, selftest; exit codes 0–7 | 7.4 |

## 4. Milestones

Milestone gates mirror the PRD's phase gates (§18). Nothing starts before the previous gate passes.

| Milestone | Contents | Gate |
| --- | --- | --- |
| **M0 — Engine core (this drop)** | Schemas + validation, hash-chained event log, mock provider, Chat Completions + Responses adapters, context assembly with budget and compaction ladder, planner with repair loop, linter, workspace jail + broker + Rule of Two, sequential scheduler with gates, failure table, caps, resume, rerun, export, CLI, self-test, full test suite | All unit + integration tests green; acceptance scenarios AC-02…AC-10, AC-13 pass against the mock provider; `studio selftest` passes end to end |
| **M1 — Real servers** | Nightly contract tests against LM Studio, Ollama, llama.cpp, vLLM, Unsloth (small models); recorded-conversation suite; engine loopback HTTP API (7.5) with per-launch token; fault-injection harness (kill/drop/malformed/disk/clock); 100-goal benchmark + reference model; seeded erosion suite wired to CI | PRD Phase-1 gate |
| **M2 — Builder UI** | Tauri shell; screens S1–S13; keychain integration; canvas + list view with full keyboard parity; WCAG 2.2 AA checks each build; context preview parity test | PRD Phase-2 gate (SUS ≥ 80 with ≥ 8 users) |
| **M3 — Tools and control** | P1 tools hardened: OS-level sandbox for shell (no network by default), domain-allowlisted web fetch, MCP client (2026-07-28, stdio, allowlisted), attachments + text extraction (TLS-8); 200-case injection corpus; path-traversal suite extended | PRD Phase-3 gate (zero denied actions executed) |
| **M4 — Hardening and release** | Signed installers (Windows/macOS/Linux) + SBOM + provenance in CI; signed update manifest with anti-rollback (D8); accessibility audit; pentest; docs (quickstart, user guide, security guide, schema reference); support policy; CRA tabletop exercise | PRD Phase-4/5 gates → GA |

### What M0 deliberately defers

- Server-side model lifecycle (preload/unload, PRV-8) — adapter hooks exist, endpoints verified in M1.
- Engine HTTP API (7.5) — the engine is driven in-process by the CLI in M0; the API is a thin wrapper in M1.
- Derived SQL tables for tasks/attempts/handoffs (spec 6.4) — M0 replays a run's events on open (correct, slower at scale); derived tables with a rebuild path land in M1.
- OS keychain, attachments, MCP, web search UI, OTLP export, run comparison, replay mode, P2 parallel branches.
- Updater (AC-14) — M4, with the signed-manifest design already fixed by D8.

## 5. Testing strategy (M0)

- **Deterministic mock provider** speaks the adapter contract exactly; tests script per-turn
  behaviour (text, tool calls, malformed handoffs, loops, over-budget usage, crashes).
- **HTTP mock server** (stdlib `http.server`) exercises the real Chat Completions / Responses
  adapters: SSE parsing, tool-call delta accumulation, 401/5xx/timeout normalization, and the
  Unsloth `enable_tools:false` policy (AC-09).
- **Crash simulation** uses a `SimulatedCrash` exception that propagates out of the driver
  leaving a truncated event log; a fresh engine instance must replay, mark the orphaned
  attempt aborted and continue (AC-06) without rerunning finished tasks.
- **Acceptance scenarios** from spec §10 that do not need a GUI or a live server are encoded
  as tests: AC-02 (repair), AC-03 (constraint extraction), AC-04 (isolation canary),
  AC-05 (constraint preservation), AC-06 (resume), AC-07 (caps), AC-08 (Rule of Two),
  AC-09 (provider tools), AC-10 (compaction), AC-13 (rerun). AC-01/11/12/14 land with M2/M4.
- **Erosion checks**: constraint text is compared by SHA-256 of whitespace-normalized text at
  every handoff (H-03), and compaction is provably unable to touch pinned blocks.

## 6. Invariants the engine must never break

1. Exactly one active session per run; tasks run in plan order (RUN-2).
2. A task's context contains only blocks 1–7 of spec 5.4 — nothing else (RUN-1, AC-04).
3. Binding constraints travel verbatim; a dropped or reworded one fails validation (RUN-3, H-03).
4. Compaction never touches: binding constraints, the task block, the goal, status fields, the system block (5.4).
5. Every state change is an event, written before the next action, hash-chained (RUN-5).
6. No run overshoots a cap by more than one model call (RUN-8, AC-07).
7. Provider-side tools are never enabled; Unsloth always sends `enable_tools:false` (PRV-5, AC-09).
8. Flags A/B/C are computed by code from granted tools and sources, never from model claims (5.9).

## 7. Risks carried into the build

| Risk | Mitigation in M0 |
| --- | --- |
| Small models emit malformed plans/handoffs | Repair loops (3 rounds planner, 1 round handoff), lenient JSON parsing, longest-valid-prefix fallback |
| Constraint erosion between tasks | H-03 SHA-256 checks + compaction pinning + tests that attack exactly this |
| Context overflow on long chains | Budget check before every attempt + ladder + `RUN-CONTEXT-OVERFLOW` |
| Sandbox escape via workspace | Jail with realpath checks on every operation, symlink resolution, size caps, traversal test suite |
| Event-log tampering / corruption | Hash chain verified on load; tampered run refused (spec 12) |
| Provider drift (Unsloth is Beta) | Adapters normalized behind one contract; presets carry quirks; probes decide capabilities |

## 8. Definition of done for M0

- `python -m unittest discover tests` green on Python 3.10–3.12.
- `python -m las selftest` runs the built-in three-task plan against the mock provider and passes.
- `python -m las run` executes a plan end to end: lint → gates → tasks → handoffs → export.
- Every invariant in §6 has at least one test that fails if it is broken.
- README covers quickstart, CLI reference, architecture and security notes.
