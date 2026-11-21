# Requirements coverage matrix

Every functional requirement from the product requirements document,
mapped to the engine implementation as of 2026-11-21 (commit 581b2c3,
281 tests green). Status: **done** (implemented and tested in this
repository), **engine-side** (the engine part is done; the visible part
belongs to the desktop UI phase), **partial**, **open** (with the honest
reason). Verification: the test suite, module-removal tests and
`python3 -m las selftest`.

## 9.1 Providers and models (PRV)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| PRV-1 | P0 | engine-side | Profiles + six presets, wire-tested against the scripted mock server (`tests/test_adapters.py`); real-server nightly suite is open work (no live server in this sandbox) |
| PRV-2 | P0 | done | `AUTH` normalised with the exact fix from the message catalogue (`las/shared/messages.py`, `tests/test_adapters.py`) |
| PRV-3 | P0 | done | Chat Completions + Responses adapters over one internal message model; full history every request, no server conversation state |
| PRV-4 | P0 | done | Capability probe with 24 h cache and fallbacks (`las/modules/providers/probe.py`, `tests/test_probe.py`); degrade-not-fail tested |
| PRV-5 | P0 | done | `enable_tools: false` on every Unsloth request (preset `extra_body`, `tests/test_adapters.py`) |
| PRV-6 | P0 | done | Roles with per-task `model`/`params` overrides honoured by the driver and shown in `inspect` (schema 6.1; `tests/test_edit_task.py`) |
| PRV-7 | P0 | done | Streaming with cancel; reasoning stored per attempt (`reasoning.txt`), never in handoffs |
| PRV-8 | P1 | done | `models load/unload` via llama.cpp router endpoints (spec 7.2); LM Studio native paths are a spec open item and are refused with that explanation, not guessed (`tests/test_models.py`) |
| PRV-9 | P1 | open | Embeddings; no consumer in the engine yet |

## 9.2 Planner (PLN)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| PLN-1 | P0 | done | Schema v1 validated on every entry (`tests/test_plan_validation.py`) |
| PLN-2 | P0 | done | V-rules, three repair rounds, invalid plans never reach the scheduler (`las/modules/planning/`, `tests/test_planner.py`) |
| PLN-3 | P0 | done | JSON-schema constrained decoding with prompt-based fallback |
| PLN-4 | P0 | done | Constraint extraction with ids; constraints listed for review at plan time |
| PLN-5 | P0 | done | Granularity, max task count, stage barriers, optional critique pass |
| PLN-6 | P1 | done | `replan`: proposal stored, diff approved by the user, user-edited tasks pinned (`Planner.replan`, `Engine.apply_replan`, `tests/test_replan.py`) |
| PLN-7 | P1 | done | Attachments: manifest with summaries into the planner prompt, never whole files (`tests/test_attachments.py`) |

## 9.3 Visual builder (BLD)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| BLD-1..BLD-6, BLD-8 | P0/P1 | open (UI) | Canvas, inspector, undo/redo, stages: desktop shell phase (blocked: no Rust toolchain in this sandbox). Engine foundations exist: versioned plan JSON import/export, plan storage |
| BLD-7 | P0 | done | Lint rules L-01..L-09 including predicted overflow and orphan constraints (`las/modules/planquality/linting.py`) |
| BLD-4 (engine part) | P0 | done | Rule-of-Two capability profile computed per task; three-of-three without a gate is an error (L-09) |

## 9.4 Agent runtime (RUN)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| RUN-1 | P0 | done | Session isolation with the canary test (`tests/test_context.py`) |
| RUN-2 | P0 | done | Strictly sequential; one active run in the API coordinator |
| RUN-3 | P0 | done | Handoff contract; constraints carried verbatim, H-rules reject drops/rewording |
| RUN-4 | P0 | done | Collector budget + compaction ladder, pinned fields untouched |
| RUN-5 | P0 | done | Hash-chained event log, resume after crash; the 1,000-kill benchmark is open work (needs long-running hardware) |
| RUN-6 | P0 | done | start/pause/cancel/retry/skip/rerun + **edit pending tasks** as `task.edited` events applied on replay (`tests/test_edit_task.py`) |
| RUN-7 | P0 | done | Retries with backoff, timeouts, repair, loop detection, max turns, stop/skip/ask policy |
| RUN-8 | P0 | done | Hard caps; overshoot bounded by one model call |
| RUN-9 | P0 | done | Context budgeting, 80% warning, compaction before overflow |
| RUN-10 | P1 | open | Verification session; the spec defers its details to the phase that delivers it |
| RUN-11 | P1 | partial | Model id, params, request hashes and prompts stored per attempt; replay mode open |
| RUN-12 | P2 | open | Opt-in parallel branches |

## 10.1 Tools and workspace (TLS)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| TLS-1 | P0 | done | Per-run jailed workspace; path-traversal suite (`tests/test_workspace.py`) |
| TLS-2 | P0 | done | Permission broker, per-task allowlists, audit events |
| TLS-3 | P0 | done | Native calling + strict text protocol; one repair then fail |
| TLS-4 | P0 | done | Untrusted-content labelling in context and transcript |
| TLS-5 | P1 | open | Shell sandbox (phase-specified; no consumer UI yet) |
| TLS-6 | P1 | open | MCP client (spec defers details to its phase) |
| TLS-7 | P1 | open | Web fetch/search |
| TLS-8 | P1 | done | Import of Markdown, TXT, CSV, JSON with text extraction; PDF/DOCX refused honestly until the desktop importer (`tests/test_attachments.py`) |

## 10.2 Human in the loop (HIL)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| HIL-1 | P0 | done | Gates with context preview, approve/edit/reject (CLI + API) |
| HIL-2 | P0 | done | Handoff edits stored as `handoff.edited` revisions with a reason |
| HIL-3 | P0 | done | `constraint.released` records who/what and evidence; downstream treats unreleased as active |
| HIL-4 | P1 | open | Desktop notifications (UI phase) |

## 10.3 History and observability (OBS)

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| OBS-1 | P0 | done | `list` with filters and JSON output, served from a derived summary table that replays a run only when new events appeared (`tests/test_summary_cache.py`) |
| OBS-2 | P0 | done | `inspect` per task incl. attempts and raw JSON |
| OBS-3 | P0 | engine-side | Events carry usage and time-to-first-token; the timeline view is UI |
| OBS-4 | P0 | done | Markdown/JSON export with provenance and AI notice |
| OBS-5 | P1 | done | Optional OTLP export, pinned GenAI conventions, off by default, local endpoints only (`las/modules/telemetry/`, `tests/test_telemetry.py`) |
| OBS-6 | P1 | done | `compare A B`: plan, constraint and output diff (`tests/test_compare.py`) |
| OBS-7 | P0 | done | Redacted diagnostics bundle, contents listed before saving, nothing sent (`tests/test_diagnostics.py`) |

## 10.4-10.7 Platform

| ID | P | Status | Evidence |
| --- | --- | --- | --- |
| LIB-1 | P0 | done | The four built-ins the PRD names + save-as-template with variables (`tests/test_library.py`) |
| LIB-2 | P0 | done | Role profiles with validated tool allowlists |
| LIB-3 | P1 | partial | Prompt version history done (capped revisions); the model licence in the picker needs server data + UI |
| SET-1 | P0 | partial | Strings externalised (`las/shared/messages.py`); themes/shortcuts are UI |
| SET-2 | P0 | done | Data location, retention (keep forever/90/30), `data prune`, `delete RUN --confirm`, `data delete-all` |
| SET-3 | P0 | done | No telemetry; connections only to configured providers; API loopback-only with token |
| SET-4 | P0 | partial | Policy published ([release-policy.md](user/release-policy.md)); signed-update machinery is packaging |
| API-1 | P0 | done | Full CLI with JSON output and meaningful exit codes |
| API-2 | P1 | done | Loopback API with token auth, `/openapi.json`, local completion hooks (`tests/test_api_extras.py`) |
| DST-1 | P0 | open | Installers/signing: desktop shell phase (blocked in this sandbox) |
| DST-2 | P0 | engine-side | Port detection (`providers detect`), self-test (`selftest`); the wizard UI is packaging |
| DST-3 | P0 | done | [quickstart](user/quickstart.md), [user guide](user/user-guide.md), [troubleshooting](user/troubleshooting.md), [plan schema reference](user/plan-schema-reference.md), [security guide](user/security-guide.md) |
| DST-4 | P0 | done | [Release channels and support policy](user/release-policy.md) |

## Non-functional (section 11)

Performance, reliability and accessibility targets are measured by the
benchmark and the platform matrix, which need real hardware and are open
work (PROGRESS.md keeps the list). The engine-side NFRs that are testable
here are covered: determinism under the mock, crash resume, the erosion
suite (constraints survive summaries/compaction), prompt-injection checks
against the broker audit log, and the architecture rules that keep the
codebase modular.
