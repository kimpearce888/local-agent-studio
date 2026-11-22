# BUILD_STATUS

Last updated: 2026-11-21 (mandate re-baseline session)

## Commits

- Mandate baseline (stated in the instruction): `3fe2d95b6f9f203252e7166f19c8cb8f0debc1d5`
- Actual current baseline at mandate receipt: `b11a8c7` (v0.3.0: complete
  tkinter GUI over the Python engine) - the baseline has moved; the
  Python engine + CLI + GUI (13 screens, 326 tests green) is preserved
  as the behavioral reference and migration source, per mandate 4.1.
- Current HEAD: see `git log -1`.

## Product architecture (target, per the delivery mandate)

Independent persistent multi-agent desktop environment, Rust-first:

- `rust/crates/las-core` - engine: agents (identity, profile versions,
  sessions, memory, inbox), communication (messages with explicit
  delivery states, busy-agent modes, calls, delegation, control
  transfer, channels, blackboard), runtime (tokio actor per agent,
  per-session turn locks), providers (OpenAI-compatible adapters,
  shared inference scheduler), context (assembly, compaction,
  rollover, memory retrieval), storage (SQLite WAL, reads the existing
  Python event-log schema), security (permissions, Rule of Two).
- `rust/crates/las-desktop` - egui/eframe desktop shell (ADR-002) with
  the mandate's 14 navigation areas; UI logic verified headlessly with
  egui_kittest (CPU renderer) in this environment.
- `las/` (Python) - reference engine + CLI + tkinter GUI (v0.3.0).
  Kept runnable for parity tests and migration; NOT the production
  engine (mandate 4.1).

## Audit of the 24 known gaps (mandate section 18) - code evidence

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Workflow-only product definition | OPEN (architectural) | pivot to the agent-first model, Rust core in progress |
| 2 | No independent agent runtime/mailbox/messaging | OPEN | Rust `las-core` agents+communication (this session) |
| 3 | Role profiles ≠ agents | OPEN | same as #2 |
| 4 | Desktop shell missing | PARTLY: tkinter GUI v0.3.0 exists as UX reference; Rust shell is the target | `las/app/gui/`, 22 Xvfb tests |
| 5 | No dynamic compaction/rollover lifecycle | OPEN | context module has an attempt-start budget ladder; session-growth rollover lands in Rust context module |
| 6 | Driver passes `token_counter=None` | **FIXED** | `driver.py:_assemble` now passes its adapter-backed counter; `context` falls back to estimates and demotes `exact_counts`; tests `test_mandate_fixes.TestItem6` |
| 7 | `max()` instead of cumulative input usage | NOT REPRODUCIBLE on current main | `runstate.py:111` accumulates with `+=`; full-suite replay tests green; kept under observation |
| 8 | Supporting calls unaccounted | **FIXED** | new `support.usage` event type; the summarizer records its tokens; `RunState.support_tokens` + `tokens_used()` include them (run caps read `tokens_used()`, so caps now cover support calls); tests `TestItem8` |
| 9 | Tasks share the run workspace | OPEN (design) | per-run workspace is per the old spec; the agent-first model gives per-agent/per-task workspaces (Rust) |
| 10 | Shell tool without OS sandbox | OPEN | subprocess execution remains flagged; Rust `security` module will enforce Landlock (kernel 5.10 present) + argument arrays; until then run_command stays deny-by-default |
| 11 | Empty allowlist = unrestricted | **FIXED** | `tools.py` denies with `command-allowlist-empty` when shell is enabled but the allowlist is empty (fail closed); tests `TestItem11` |
| 12 | Web fetch SSRF/IP/redirect hardening | OPEN | broker gates the tool; the hardened fetcher (resolved-IP checks, redirect revalidation, private/link-local denial, response limits) lands in the Rust tools module |
| 13 | Credentials as plaintext | LARGELY ADDRESSED | keys are `env:NAME` references, never values; serializer audit pending in Rust port |
| 14 | Registry one-level nesting limit | OPEN | `registry.py:104` rejects deeper descendants; the Rust module tree designs recursive nesting from the start; Python registry fix deferred to the port (no user impact today) |
| 15 | Attempt state/retry termination | FIXED (prior) | attempt state machine; 1000/1000 kill-resume benchmark |
| 16 | Pause/resume/cancel lifecycle | FIXED (prior) | same benchmark + run view tests |
| 17 | API coordinator keeps control access | FIXED (prior) | `las/modules/api/__init__.py` holds the live driver for paused/gate runs; `tests/test_api.py` |
| 18 | Resume restores original config | **FIXED** | `DriverConfig.to_public_obj` frozen into `run.created`; `Engine.driver` restores models+config when not given; CLI resume passes explicit-flag overrides only; GUI resume restores; tests `TestItem18` |
| 19 | Capability tests on real servers | BLOCKED (environment) | no model server in this sandbox; harness shipped for the user's machine |
| 20 | Benchmark is mock-only | BLOCKED (environment) | no reference model available; mock benchmark pins determinism only |
| 21 | providers.mock removal CI failure | FIXED (prior) | removal tests pass on main |
| 22 | CI matrix + packaging smoke | PARTLY | CI green; `.deb` via `dpkg-deb` planned; AppImage tooling check pending |
| 23 | Runtime/package version agreement | OPEN | Python `0.3.0` vs Rust workspace `0.4.0`; unify at release |
| 24 | Docs reflect reality | IN PROGRESS | this file + HANDOVER.md added; full doc pass at release |

## Decisions (ADRs in `docs/adr/`)

- ADR-002: desktop stack = egui/eframe (Tauri 2 blocked in the build
  environment: no webkit2gtk-4.1/gtk+-3.0/libsoup-3.0 pkg-config
  entries and no passwordless sudo to install them). egui is fully
  Rust including rendering; UI logic is verified headlessly with
  egui_kittest. On a stock Ubuntu 22.04 desktop (the mandate's target)
  egui runs with no extra system dependencies.
- ADR-003: the on-screen window cannot open in THIS sandbox
  (`libxkbcommon-x11` absent; base `libxkbcommon.so.0` present).
  Evidence captured; the binary itself compiles and its UI is tested
  headlessly. This is an environment limit, not a product limit.

## Current state of the Rust product engine (this session)

`rust/crates/las-core` - the agent-first domain, all tested:

- **storage.rs** (10 tests): SQLite WAL; agents, immutable
  profile-version history, sessions + canonical transcripts, versioned
  memory, durable inbox with delivery states + idempotency, channels,
  blackboard, comm_rules topology (default-deny agent→agent); opens
  the Python engine's DB alongside (single data dir, both engines).
- **agents.rs** (7 tests): full lifecycle - create, edit-as-new-
  version, duplicate, enable/disable, status transitions, archive,
  reset-session-keeps-identity-and-memory, delete with
  KeepMemory/DeleteAll retention, JSON export.
- **communication.rs** (8 tests): send_message, agent_call with
  correlated responses, delegate with lineage-based cycle + depth
  protection, transfer_control, channel posting with membership +
  topology fanout; everything persisted before delivery.
- **runtime.rs** (6 tests): tokio actor per agent, per-session turn
  locks, queue/wait busy modes, turn-boundary delivery with canonical
  transcript appends, restart recovery of undelivered entries,
  disabled agents receive nothing.
- **scheduler.rs** (5 tests): per-(provider,model) bounded
  concurrency, round-robin fairness across agents (a spamming agent
  cannot starve others), per-agent inflight caps, cancellation skips
  waiters; slot handoff by permit ownership transfer (no drift).

`rust/crates/las-desktop` - the egui shell (ADR-002/003): fourteen
navigation areas (mandate section 14); Dashboard/Agents/Inbox wired to
real las-core data (real create/toggle/compose actions); unbuilt areas
show an honest construction note, no fake controls. 5 egui_kittest
headless tests (render + click-through + the harness's own
accessibility check). Release build green.

Acceptance scenarios landed: 19.1 (four independent agents, one shared
endpoint, independent sessions/transcripts/memory, bounded shared
concurrency) and 19.2 steps 1-6 (message to a busy agent queued,
delivered at a turn boundary, acked, answered, all visible after a
full restart) - as integration tests in
`rust/crates/las-core/tests/acceptance.rs`.

## This session's additions (commit chain after 04b02c9)

- **providers.rs**: OpenAI-compatible adapter over ureq 3
  (`list_models`, `chat` with usage, `probe`) for LM Studio / Ollama
  / llama.cpp / vLLM / Unsloth Studio / LAN endpoints; API keys are
  runtime-only, never persisted. Verified against a REAL loopback
  HTTP server (stoppable accept loop; no external service).
- **runtime**: async responder; `production_responder` wires every
  real model turn through the scheduler (fair acquire) and the
  blocking HTTP call (`spawn_blocking`).
- **storage**: SQL-atomic transcript seq allocation (fixed a genuine
  cross-runtime race found by acceptance 19.2); `rollover_session` +
  `agent_history` (session continuity, 19.3).
- **migration.rs**: reads the Python engine's `events` table and
  verifies the FULL hash chain with the exact Python algorithm
  (sha256 + canonical JSON). Verified against a fixture generated by
  the real Python engine (18 events, chain-checked in both engines);
  tampering is detected and refused; fresh data dirs return empty.
- **Desktop**: six live screens now (Dashboard, Agents, Inbox,
  Sessions & Memory, Collaboration Channels, Teams/Network topology
  editor) - all controls perform real las-core actions; 9 kittest
  headless tests including click-through behavior, accessibility
  names and an ID-clash fix (egui's debug overlay caught duplicate
  ScrollArea auto-ids).
- **Acceptance**: 19.3 (rollover + restart), 19.4 (memory with
  provenance + versioned edits + explicit deletion), 19.5
  (delegation → durable artifact → report back through the inbox),
  19.6 (two models with independent, fair capacity) all green.
- **Packaging**: `scripts/make_deb.py` (repo) builds the .deb with
  dpkg-deb (no root needed): control, .desktop entry, generated icon,
  release binary; structural verification (info/contents). Actual
  installation testing needs root - environment-blocked, recorded.
- **Environment note**: the 10 GB sandbox disk filled up (5.3 GB
  debug + 1.2 GB stale release in rust/target). Cleaned; slim build
  profiles added to rust/Cargo.toml (debug=0, incremental=false).

## Test results

- Rust: 58 passed (43 las-core unit + 2 acceptance wave 1 + 4
  acceptance wave 2 + 9 UI), `cargo test --workspace` green.
- Python reference engine: 313 tests green headless (326+ with the
  tkinter GUI suite under Xvfb).

## Blockers (honest)

- No model server / reference model in the sandbox: real-provider
  capability tests and real-model benchmark quality remain unverified
  (audit items 19, 20).
- No passwordless sudo: cannot install GTK/WebKit (Tauri) or
  xkbcommon-x11 (on-screen egui window in-sandbox only - ADR-003).

## Exact next engineering action

Wire the desktop to the production responder and the Python-run
history: a Providers & Models screen that registers real endpoints
(base URL + optional env-referenced key) with capability probing, the
runtime started from the desktop (agents actually conversing through
the scheduler), and a Run History view over `migration::legacy_runs`.
Then 19.7 (permission surfaces in the UI) and 19.8 (the full desktop
walk-through), AppImage alongside the .deb, and the version
unification (0.4.0 everywhere).
