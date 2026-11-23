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
| 12 | Web fetch SSRF/IP/redirect hardening | **FIXED** | `tools.rs` hardened fetch: domain allowlist + resolved-IP checks (private/loopback/link-local/ULA/IPv4-mapped refused, SSRF), manual redirects re-validated per hop (max 3), text-only MIME gate, 1 MB / 10 s caps; tests include a real local HTTP server covering loopback refusal, same-host redirect follow, cross-host redirect refusal and binary refusal |
| 13 | Credentials as plaintext | ADDRESSED | keys are `env:NAME` references, never values; serializer audit CLOSED: error paths never echo the key, DB/API/JSON carry the env NAME only (test-proven) |
| 14 | Registry one-level nesting limit | ADDRESSED | `registry.py` now nests recursively (discovery walks the whole tree) with parent-chain cycle detection and a 64-level sanity guard |
| 15 | Attempt state/retry termination | FIXED (prior) | attempt state machine; 1000/1000 kill-resume benchmark |
| 16 | Pause/resume/cancel lifecycle | FIXED (prior) | same benchmark + run view tests |
| 17 | API coordinator keeps control access | FIXED (prior) | `las/modules/api/__init__.py` holds the live driver for paused/gate runs; `tests/test_api.py` |
| 18 | Resume restores original config | **FIXED** | `DriverConfig.to_public_obj` frozen into `run.created`; `Engine.driver` restores models+config when not given; CLI resume passes explicit-flag overrides only; GUI resume restores; tests `TestItem18` |
| 19 | Capability tests on real servers | BLOCKED (environment) | no model server in this sandbox; harness shipped for the user's machine |
| 20 | Benchmark is mock-only | BLOCKED (environment) | no reference model available; mock benchmark pins determinism only |
| 21 | providers.mock removal CI failure | FIXED (prior) | removal tests pass on main |
| 22 | CI matrix + packaging smoke | **FIXED** | CI green (badge: passing); `scripts/make_deb.py` and `scripts/make_appimage.py` rebuild BOTH artifacts from the release binary and verify them structurally (unpack/self-extract + checksum); both re-run green for 0.4.0 |
| 23 | Runtime/package version agreement | **FIXED** | unified at 0.4.0: `pyproject.toml`, `las.__version__`, Cargo workspace, both artifact filenames |
| 24 | Docs reflect reality | **DONE (v0.4.0)** | BUILD_STATUS + HANDOVER final pass; `desktop-app.md` gained the full egui-shell section (14 live areas, tool engine, approval gate); release-policy covers the installer pipeline; packaging scripts persisted |

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

## This session's additions (after d8e14e0)

- **Providers & Models screen** (live area #7): register
  OpenAI-compatible endpoints (persisted `provider_endpoints` table;
  the key is stored as an ENV-VAR NAME only, the secret is read at
  runtime start and never touches disk), discover models over REAL
  HTTP, probe each model with a real chat call, start/stop the
  runtime.
- **Runtime bootstrap** (`bootstrap.rs`): one background thread owns
  a current-thread tokio runtime + `AgentRuntime` with the production
  responder; it polls `pending_inbox()` (entries born "accepted" -
  the first poll-state bug was caught by 19.8 and fixed) and delivers
  every entry through the fair scheduler and the blocking HTTP call.
- **Activity & History screen** (live area #8): recent deliveries
  from the agent store + the VERIFIED legacy run history
  (`migration::legacy_runs`/`legacy_events`, hash chain verified on
  read, a broken chain is reported, never hidden).
- **19.7 green**: revoking a rule through the Teams screen restores
  default-deny AND the engine actually refuses the message; tool
  grants are explicit checkboxes (nothing implicit) and land in the
  profile.
- **19.8 green - the full desktop walk-through**: a kittest test with
  a REAL loopback HTTP server: register endpoint via UI -> start
  runtime via UI -> compose a message via UI -> the background thread
  delivers it through the scheduler and a genuine HTTP round-trip ->
  the model reply lands in the transcript -> visible on the Sessions
  screen -> the acked delivery shows in Activity -> clean stop via
  UI. Two real bugs were found by this scenario and fixed (the
  accepted-vs-poll-state mismatch and a checkbox temp-binding bug).
- **AppImage**: `scripts/make_appimage.py` builds
  `local-agent-studio-0.4.0-x86_64.AppImage` (10.6 MB) with the
  extracted appimagetool (no root, no FUSE needed to BUILD);
  verification = self-extraction + payload comparison. RUNNING it
  needs FUSE on the target machine - not testable in this sandbox.
- **Version unification**: 0.4.0 everywhere (Cargo workspace already
  0.4.0; pyproject.toml, `las.__version__`, and the meta table's
  app_version now tracks the package version instead of a stale
  hardcoded 0.1.0).

## Test results

- Rust: 62 passed (44 las-core unit + 6 acceptance 19.1-19.6 + 12 UI
  including 19.7/19.8), `cargo test --workspace` green.
- Python reference engine: 313 tests green headless (326+ with the
  tkinter GUI suite under Xvfb).

## CI repair (after 85a127d)

The GitHub Actions workflow (Python matrix 3.10/3.11/3.12) was red on
most runs. Three root causes, all reproduced locally and fixed:

1. `tests/test_gui.py` imported tkinter UNCONDITIONALLY at module
   level; actions/setup-python builds ship WITHOUT _tkinter, so
   unittest collection crashed before a single test ran (all three
   matrix jobs red). Fix: the tkinter/StudioApp/PALETTES imports moved
   behind a try/except and a `setUpModule` skips the whole module
   honestly ("tkinter not available in this Python build" / "no
   DISPLAY"); verified by stubbing `sys.modules['tkinter'] = None`.
2. `tools/removal_test.py` copied the WHOLE repo into /tmp including
   the multi-GB `rust/target` build tree - "No space left on device".
   Fix: IGNORE now also excludes target/, node_modules and caches.
3. Disposable-module removal was red: `s12_export.py` imported
   reporting at module level, and `s1_wizard.py` / `bench/harness.py`
   referenced removed modules without the documented degradation
   marker. Fixes follow the existing optional-dep pattern: deferred
   imports + `# optional-dep: ...` tags + a graceful error dialog
   (export); `bench/harness.py` gains a `_mock()` helper and
   `tests/test_bench.py` declares its functional mock dependency so
   the removal script deletes it with the module.

Verification, on a clean rsync checkout with no build artifacts:
discovery x2 exit 0 (313 tests OK headless, 5 GUI skips), removal x3
exit 0 ("REMOVAL VERIFIED" each), packaging smoke: the wheel builds,
installs, `studio --version` -> 0.4.0, `import las` -> 0.4.0. The
no-tkinter simulation gives 0 collection errors with the GUI module
skipped. With Xvfb the full GUI suite runs too: 335 tests OK.
No 3.11+-only syntax exists anywhere (3.10 matrix safe).

## Context assembly + reply routing + Tools screen (after 5d8b0f0)

- **Token-budgeted context assembly** (`las-core/src/context.rs`,
  mandate 9): a real model turn now sees the system prompt, the
  agent's long-term memory (newest first, capped at a quarter of the
  budget, 256 tokens per note) and the recent transcript (newest kept
  so oldest turns drop first, emitted chronologically), then the
  inbound message, all under a token budget (default 8192,
  `context_budget` in profile params overrides). Tokens are a
  documented ~4 chars/token approximation - a safety cap, not an
  invoice; no tokenizer dependency.
- **Reply routing** (mandate 7): when the sender is another agent,
  the reply is persisted as a NEW inbox entry for the sender -
  persisting IS the routing, so send_message's topology check applies
  for real. A missing reverse rule refuses the reply and the refusal
  is recorded in the agent's transcript as a system note, never
  silently lost. Two integration tests: a bidirectional loop closes
  (a -> b -> a -> b round-trips) and a one-way topology refuses with
  a recorded refusal.
- **Tools & Integrations screen** (live area 9/14): the grant
  vocabulary with per-tool documentation, the per-agent grant matrix
  read from the persisted profiles, and an honest note that tool
  EXECUTION (permission broker, Rule of Two) is under construction -
  no fake toggles.
- `docs/user/release-policy.md` now documents the 0.4.0 desktop
  artifacts (.deb + AppImage), how each is verified, and what CI
  gates on every push.

Rust: 69 tests green (50 lib incl. 4 context + 2 routing, 6
acceptance 19.1-19.8 scenarios in 3 files, 13 UI). Python: 335 OK
(313 headless + GUI under Xvfb).

## Tool engine + Projects screen (after b617c92)

CI status: run 29 (b617c92) is GREEN on GitHub - the repair worked.
Run 28 (5d8b0f0) failed only on a 10-second poll deadline being too
tight for a throttled CI runner (test_api end-to-end still
"running"); all four such polling loops now carry a 60-second wall
budget (they still exit the moment the run is terminal).

- **Tool execution engine** (`las-core/src/tools.rs`, spec 5.9 /
  mandate 10): a permission broker with the spec's check order -
  explicit allowlist first (nothing granted implicitly), argument
  validity and the 5 MB write cap, workspace-jail containment
  (component-level traversal refusal + canonicalized-parent check),
  and per-turn call budget. Executors for read_file, write_file,
  list_files (all jailed, listing capped at 1000 entries) and
  web_fetch (domain allowlist from profile params, 1 MB / 10 s
  caps). Rule-of-Two capability flags are computed from the grants
  by code (A and C with web_fetch; B honestly false until a tool
  can reach outside the jail). Every decision persists a
  tool.called / tool.denied audit row (new tool_events table).
- **Tool loop in the actor**: a reply carrying TOOL_CALL JSON lines
  runs each call through the broker, appends the result to the
  transcript as a role-"tool" message, and gives the model another
  round (max 4 rounds / 8 calls per turn); denials are visible to
  the model so it can adapt. Integration tests: a stateful responder
  writes a real file through the loop and the audit row lands; an
  ungranted call is denied, audited, and SEEN by the model.
- **Projects screen** (live area 10/14): projects group agents
  (projects + project_members tables); membership is organization,
  never a permission bypass. Creating from the form is a real click
  (the form was deliberately re-stacked - the first horizontal
  layout pushed the Create button outside the test viewport, which
  the UI test caught).

Rust: 80 green (60 lib incl. 8 tools + 2 loop tests, 6 acceptance,
14 UI). Python: 313 OK headless.

## Production-readiness pass (after cbce96a)

The 14-area desktop shell is COMPLETE, the approval gate is in, and
web fetch is hardened.  What this pass added:

- **Four remaining screens** (14/14 areas live): Artifacts & Files
  (read-only browsing of the agent workspace jails the tool engine
  writes, 5 MB/8 KB preview caps), Tasks & Delegations (task rows
  that are ALSO delivered to the agent's real inbox; the runtime
  picks them up), Workflow Studio (plans export as plan schema v1
  JSON runnable by `python3 -m las run <file>`), Settings &
  Diagnostics (version, data dir, SQLite quick_check, row counts,
  provider endpoints).
- **Approval gate (TLS-2, spec 5.9)**: grant modes always / ask
  once / ask each / never (legacy string grants parse as always).
  An ask-mode call is DEFERRED - the model is told, the request is
  queued in pending_approvals, and nothing executes.  Approving on
  the Tools screen either upgrades the grant (ask once) or pays for
  exactly one more use (ask each, one_shot_grants consumed by the
  real execution).  Ask-gated tools do not set the Rule-of-Two
  external flags until approved.  Lifecycle tests cover defer ->
  approve -> run for both modes.
- **Hardened web fetch (audit item 12)**: see the audit table.
- **Packaging scripts persisted** (release-policy now matches
  reality): `scripts/make_deb.py` + `scripts/make_appimage.py`
  rebuild and structurally verify both artifacts; re-run green for
  0.4.0 (.deb 8.5 MB, AppImage 12 MB).  The appimagetool path uses
  the extracted form (no FUSE in the build sandbox).
- CI on GitHub: green (badge: passing) for cbce96a.

Test totals: Rust 94 green (69 las-core lib incl. 15 tools + 4
gate/loop integration, 6 acceptance, 19 kittest UI); Python 313 OK
headless (skips recorded honestly).

## CI hardening + flake investigation (after 7b21a65)

- **The Rust workspace is now IN CI** (was Python-only): a `rust`
  job on stable runs `cargo test --workspace --locked` (engine,
  parity, acceptance, headless egui_kittest UI) plus a release
  build of the desktop shell.  First run (32) was red: the Python
  engine migration fixture was excluded by the blanket `*.db`
  gitignore rule, so the two migration tests passed locally and
  failed on every fresh checkout.  The fixture is now committed
  with an explicit gitignore exception; the rust job is green in
  runs 33-36.
- **A regression of ours, caught by CI (run 34)**: the crash-test
  insertion in 2fd8e50 split `test_events_after_seq_polling` from
  its `@skipUnless(providers.mock)` decorator (the decorator
  attached to the NEW test), so in the providers.mock removal copy
  the events test ran undecorated and failed with KeyError
  'run_id'.  Fixed in 6a4d58b; all three removal verifications pass
  locally again.
- **Flake ROOT CAUSE FOUND AND FIXED (runs 33/35/38/39, 3.12
  job, removal-copy suites)**: the stack-recording diagnosis (run
  39) caught it red-handed - `IndexError: tuple index out of range`
  at `storage._row_to_event(r["event_id"])` from the RUN thread.
  The Python Store shared ONE sqlite3 connection between the API
  handler threads and the run-coordinator thread
  (`check_same_thread=False` only disables the check; concurrent
  use must be serialized by the application).  A poll-side SELECT
  iterating the shared connection while the run thread executed
  and committed could hand the reader a zero-column Row; in the
  run thread this crashed the run (caught since 2fd8e50 and
  recorded as RUN-THREAD-CRASHED), and the same contention window
  explains the run-33/35 stuck-at-running faces.  FIX: every
  public Store method is now serialized through a reentrant lock
  (storage `_locked`); a stress test (300 appends vs 2x300
  read+verify threads) FAILS 6/7 runs with the lock disabled and
  is deterministically green with it - the race reproduces locally
  now, so the regression test genuinely guards the fix.  315
  Python tests OK; all three removal verifications green.
- Runs 36/37 (72486c8, c0fadf9): all four jobs green.

## Session: rollover + MCP client (after c0fadf9)

Note: this sandbox came back with the repo directory reverted to an
old v0.3.0-era snapshot (stale reflog included); the GitHub remote
still had c0fadf9, so a fast-forward restored the working tree with
nothing lost.  The Rust toolchain had to be reinstalled (rustup,
stable, cargo 1.100.0).

- **Automatic session rollover (mandate 9.4)** - `las-core/src/
  sessions.rs`, wired into the actor's turn path: the active
  segment is resolved per turn (find-or-create `-s-main`), and when
  the transcript exceeds the agent's policy the rollover happens
  BEFORE the inbound is appended.  `perform_rollover` creates the
  next segment with a code-extracted structured continuity summary
  as its head (pinned instructions + unresolved requirements
  verbatim, decisions, references/artifacts, recent tail), marks
  the prior segment `rolled_over` with a closing note, and chains
  `segment_of`.  `session_lineage` orders the segments; the
  desktop's session combo already lists every segment with its
  status, so prior segments are user-inspectable.
  `restore_segment` resumes from an earlier segment by branching a
  new one (append-only history is never rewritten).  The summary is
  deterministic code extraction, not model-generated - documented
  as such (no model server exists in this build sandbox).
- **MCP client, stdio transport (mandate 13.2)** - `las-core/src/
  mcp.rs` + broker/runtime wiring: configs persist in a new
  `mcp_servers` table (`enabled` is the user's connection approval,
  stored separately from the config so an import can never imply
  it); the client spawns the server as a child speaking
  newline-delimited JSON-RPC 2.0, does the initialize handshake,
  lists tools/resources/prompts, and calls tools with 5 s/10 s
  timeouts, crash detection and respawn (audited).  A connection is
  USABLE, not merely alive: a failed request marks the client
  broken and the next call respawns - found by a parallel-test race
  where python closes its std streams BEFORE the process actually
  exits (try_wait still said alive while the reader was gone).
  Per-server `allow_tools` fails closed; per-agent grants
  (`mcp:<server>` / `mcp:<server>:<tool>`) go through the SAME
  broker and approval gate as built-in tools; every model-visible
  result is labelled UNTRUSTED content; connect/restart/call are
  audited.  Tests run against a REAL stdio server fixture
  (tests/fixtures/mcp_echo_server.py, python3), including an
  end-to-end model turn: TOOL_CALL -> broker -> server -> labelled
  result in the transcript -> audit rows.  Not yet: Streamable HTTP
  transport, dynamic MCP tool-schema injection into model turns.

Test totals this session: Rust 112 green (87 las-core lib incl. 8
sessions + 10 mcp, 6 acceptance, 19 kittest UI; workspace
`--locked`), Python unchanged (314 at c0fadf9).

## Session: the four implementable P1 items (after 9cb4134)

- **Artifacts as versioned first-class entities (mandate 10)**:
  `las-core/src/artifacts.rs` + `artifacts` table (logical id +
  SQL-allocated version, sha256, agent ownership, JSON provenance,
  project sharing).  register/bump, history, capped preview,
  deletion (reference only - the owner's file stays), and
  `export_artifact`: a verified COPY into the recipient's workspace
  (hash re-checked at export; on-disk tampering after registration
  is refused).  Project sharing lists shared artifacts to project
  members without ever opening the jail.  The desktop Artifacts
  screen lists entities (owner/project/version/size/hash) with
  preview, register and delete buttons - kittest-verified.
- **Landlock-sandboxed run_command (mandate 13.4)**:
  `las-core/src/sandbox.rs` - raw landlock_create_ruleset/add_rule
  (PATH_BENEATH)/restrict_self syscalls in pre_exec after
  PR_SET_NO_NEW_PRIVS; jail = only writable root, system dirs
  read+execute; argv arrays only; setsid + group SIGKILL timeout;
  RLIMIT_AS/CPU/FSIZE/NOFILE; 64 KB output cap; command policy
  from profile `command_allowlist` (fail-closed when empty);
  granted per agent like every tool, ask-modes through the
  approval gate.  **Fail closed** on kernels without Landlock -
  this sandbox's 5.10 kernel runs exactly that path; CI's 6.x
  kernels run the confinement tests (in-jail write ok, escape
  write and outside-file read refused, timeout kills the tree).
- **Provider-exact token accounting (mandate 9.3)**: `token_usage`
  table records every call's exact provider counts with our
  pre-call estimate; `context::calibrate` shrinks the budget by the
  observed ratio when the provider counts MORE (labelled; never
  pretends estimates are exact); the production responder wires it
  in - verified against a real local HTTP endpoint.
- **MCP Streamable HTTP (mandate 13.2 remainder)**: transport
  trait split (Stdio/Http) behind the same client facade; `url`
  configs POST JSON-RPC, capture and resend `Mcp-Session-Id`
  (test server 400s without it), parse `application/json` and
  text/event-stream responses, treat 202 as accepted notifications,
  and mark failures for reconnect.  Registry posture unchanged
  (approval before connection, allowlists, audit, UNTRUSTED
  labels) - covered by 4 new HTTP tests against the python fixture.

Test totals: Rust 135 green (109 las-core lib, 6 acceptance, 20
kittest UI), workspace `--locked`; release build green.  Python
side untouched this session (315 at 9cb4134).

## Session: audit residuals 13/14 + MCP tool-schema injection (after 65952fb)

- **Credentials serializer audit closed (#13)**: two test-proven
  invariants - (a) ProviderError strings from network failures and
  HTTP 401s never contain the key (URLs/statuses only; the adapter
  has no Debug/Serialize impls by design), (b) the endpoints table
  persists the env var NAME; a raw byte scan of the database file
  plus the API rows plus their JSON serialization all show the name
  only.
- **Python registry recursive nesting (#14)**: the one-level limit
  is gone - discovery walks `las/modules/**` at any depth, structure
  validation now requires the parent CHAIN to be acyclic (seen-set
  walk, self-parent rejected, 64-level sanity guard).  Tests: deep
  nesting accepted, parent-cycle rejected, self-parent rejected;
  registry suite 20/20, lifecycle + CLI green.
- **Dynamic MCP tool schemas in the model context (13.2 remainder)**:
  `McpClients::tool_catalog` filters by enablement + server
  allowlist + agent grants (exact `mcp:<server>:<tool>` grants list
  only that tool), `mcp_tools_note` formats the note (exact
  TOOL_CALL format, UNTRUSTED label, 20-tool / 400-char caps), and
  `production_responder` appends it to the system prompt when the
  catalog is non-empty.  E2E: the request body captured at the
  provider shows the note in messages[0], the agent's own prompt
  still leading.

Test totals: Rust 141 green (115 las-core lib, 6 acceptance, 20
kittest UI) workspace `--locked`; Python 317 passed / 22 skipped.


## Session: v0.4.7 - the throttle guard (live-verified against a 14h+ 429 window)

The account-wide 429 window did NOT lift with the session gap: the
chat endpoint and the z-ai CLI functions (web_search) both refused
with "Too many requests" - so the limit is account-level, not
endpoint-level, and consistent with a daily quota exhaustion from the
two real-model benchmark sessions.  Live polling over 25 minutes (3
single-request probes at 7-8 minute intervals) all bounced.

What shipped instead (the measured lesson from 0.4.6, now enforced by
code, push 0fe0ffc):

- **bench throttle guard**: ONE tiny chat round-trip BEFORE the
  capability probe.  /v1/models is answered by the local proxy, so
  the old flow reported reachable+authenticated while every live chat
  call died and the corpus then burned goals as plan failures.  The
  guard aborts with exit 2 and names the recovery procedure.
  Live-verified against the hot upstream: clean abort in ~3 minutes
  (the proxy's own backoff exhausts and surfaces SERVER_ERROR),
  BEFORE any goal runs.

Rerun procedure once the quota resets (unchanged, in BUILD_STATUS
0.4.6 section): restart the proxy, then single process
  PYTHONPATH=. python3 -m bench --provider zai --model glm-4-plus \
      --goals 10 --kills 2 --out bench/results
The guard now makes that command self-protecting: it either runs the
full corpus against a healthy upstream or refuses cleanly.

## Session: v0.4.6 - the request timeout was the real task wall (and a rate-limit lesson)

Following the measured t3 TIMEOUT (goal 1, v0.4.5 partial run) to the
bottom: the z-ai SDK is NON-STREAMING, so the proxy buffers the model's
ENTIRE generation - the first byte arrives only when the turn
completes, and one big-context turn measured 300-600s.  The 300s
socket timeout (urlopen timeout -> ProviderError TIMEOUT -> the
driver's 2 retries -> task dead) killed healthy work at exactly ~300s
per attempt; the event timeline showed all three t3 attempts dying at
300-337s.  Fixes (pushed, 336 tests green):

- HttpCore and both adapter classes default their request timeout to
  300s (was 60s); the bench's --timeout defaults to 900s.
- DriverConfig gains wall_task_seconds (the 30-minute task wall was a
  DEFAULTS constant, not per-run configurable): recorded in
  run.created, restored on resume, and real bench runs set 3600s.
- bench gains --start/--end corpus slicing for parallel slice runs.

The v0.4.5 reference benchmark RERUN could not complete this session:
launching TWO corpus processes in parallel (to beat the wall clock)
tripped the upstream's rate limiter hard - 142 x HTTP 429, and the
limit did not lift for 40+ minutes even with a fully silent window
and single-request probes.  The proxy header documented this exact
upstream behavior; the mistake was mine (doubling the request rate).
A hot limit would also have burned corpus goals as plan failures, so
the bench was killed rather than allowed to run degraded.

NEXT SESSION (single process only; the upstream needs a cool window):
restart the proxy, then:
  PYTHONPATH=. python3 -m bench --provider zai --model glm-4-plus \
      --goals 10 --kills 2 --out bench/results
Expected: with the 900s request timeout and the 3600s task wall, the
gated market-brief goals should reach their approval gates (gate
pauses) instead of dying t3 TIMEOUT; csv-summary goals should
complete without help (proven live in the v0.4.5 validation slice).

## Session: v0.4.5 - the tool capability was never wired (the real 0/30 root cause)

Chasing the remaining "models invent their own tool-call syntax"
wall with live diagnosis (each finding measured, none guessed) peeled
four layers, and the last one was the true root of the 0/30:

1. **DriverConfig.tool_calling defaults True and nothing wires the
   probe's finding into it.**  The bench, the CLI and the GUI all
   passed the default through.  Against the z-ai proxy the worker got
   native tool defs the proxy silently drops, never saw the text tool
   protocol, and had NO tool mechanism at all - every "invented
   syntax" since 0.4.3 was a model improvising in the dark.  Fixed:
   bench threads caps.tool_calling everywhere; CLI --text-tools; GUI
   tool_mode setting.
2. **The text tool parser required wire tags the models drop.**  With
   text mode actually on, the model emitted perfect bare one-line JSON
   - the engine executed none of it (0 tool.called).  The parser now
   accepts bare JSON (balanced brace scan, never inside a final
   handoff message), the wrapped form for back-compat, and the two
   measured XML idioms (colon-tag; tool_code, including the bare-name
   form).  Tool results now carry a citable TOOL RESULT (call_N) id.
3. **The broker's exhaustive per-task allowlist starved under-granted
   plans** (read_file denied on t3 of 8; honest blocks "need access to
   tools to read the sales CSV").  Workspace file tools are now the
   baseline (PRD least agency: tools BEYOND workspace files are off
   until granted); shell/web still need the grant.  tool_defs and the
   Rule-of-Two profile use the same effective set.
4. **H-06 rejected verbose path citations** ("workspace file: a.csv")
   while 95% of the model's sources were real existing paths, and
   H-05 fired because nobody told the model that finishing a task
   does not resolve a constraint.  _source_ok strips the verbose
   prefixes and accepts what resolves; WORKER_PROTOCOL v2 teaches
   sources and constraint states; the planner prompt requires
   granting each task the tools it needs.

A transport artifact of this sandbox deserves a note: the tool-call
wire tags are stripped from every file Read and grep output the agent
sees, which is why the tagged requirement hid in plain sight across
two sessions of "the parser looks fine".  The debugging scripts that
finally exposed it (chr-built tag strings, escaped dumps) are kept
under scripts/ (dump_tag_regions.py, debug_tool_re.py,
fix_tool_protocol.py) as the recipe for diagnosing anything that
touches wire markup.

Live validation of the fixed chain (glm-4-plus, csv-summary family):
COMPLETED WITHOUT HELP - the first real-model completion of the
product's core loop (plan valid first attempt, real file reads,
deliverables written, valid handoffs across a multi-task chain) -
plus an honest blocked market-brief, 0 failures, 0 plan repairs.
335 tests, engine gates green.

The v0.4.5 reference benchmark (10 goals + 2 kills) was launched at
session end and the session budget ended it early: goal 1 (gated
market-brief, the hardest family) ran 22 minutes of real multi-task
work (t1/t2 succeeded with genuine file reads and writes) and died
t3 TIMEOUT - the per-task wall-time default (wall_task_seconds) was
tuned for mock-speed runs and is now too small for real multi-turn
file work.  That is the NEXT measured fix: raise the real-run task
wall budget (and let long tasks complete instead of dying at a
default), then rerun the full corpus.  Partial outcomes recovered to
bench/results/bench-real-v045-partial.json (1 goal measured: failed
TIMEOUT, 0 contract violations - the failure class has moved from
handoff/protocol violations to honest capacity limits).

## Session: v0.4.4 - the honest blocker + benchmark input provisioning

The 0.4.3 reference benchmark (0/30 completed-without-help, every
failure a handoff violation) was diagnosed to its roots and fixed on
both sides of the contract:

1. **The corpus asked for sourced facts from an empty workspace.**
   Every serious agent benchmark provisions inputs (GAIA ships
   attachments, SWE-bench ships the repo); ours asked for "cited
   figures" with no sources and no web, so an honest model had no
   legal path to completion - H-06 violations were the model's only
   route to "done".  Each of the 20 goal families now seeds
   deterministic synthetic input files into the run workspace
   (bench/goals.py FIXTURES + seed_workspace) before the run starts;
   the measured loop is the product's real one: read workspace files,
   extract file-sourced facts, write deliverables, hand off.
2. **`blocked` was not a run state.**  A valid blocked handoff was
   recorded as task.succeeded (inflating the completed metric);
   silently, a model could "complete" by blocking.  `blocked` is now a
   first-class terminal run state (events task.blocked + run.blocked);
   resuming re-attempts the blocked task once the user provides the
   missing prerequisite; the GUI shows it and allows resume; the bench
   reports blocked_runs as its own number.  H-02 now enforces the
   four-field blocker (a blocked handoff must name a blocking open
   issue with its constraint id - ownership deferral fails
   validation), and WORKER PROTOCOL v2 teaches the honest exit.
3. **Two engine traps made verbatim copying unwinnable** (found by
   live diagnosis, not guessing): the context renderer decorated each
   constraint with prerequisite/authority/fallback sub-lines, and a
   model that copied the rendered block verbatim failed H-03 by hash;
   and a "forgot the <handoff> block" reply consumed the single
   repair round, so one dropped period later killed the run.  Fixed:
   the renderer puts the constraint text on its own quoted `text:`
   line, H-03 strips exactly the engine's own decoration before
   hashing (anything else still fails), and the no-block nudge has its
   own budget (2) separate from the handoff repair (1).

Post-fix reference run (glm-4-plus, PARTIAL sample - the session
budget ended the run at 8 of 12 goals; recovered from the event store
into bench/results/bench-real-v044-partial.json): 7 runs measured,
0 completed, 0 blocked, 7 failed (6 HANDOFF-INVALID, 1 LOOP-DETECTED)
plus 1 plan-invalid.  The honest paths are now REACHABLE - during
diagnosis a live model produced a fully valid four-field blocked
handoff with a file-sourced fact (csv-summary family; it failed H-03
only on the engine's own rendered decoration, which this session then
fixed) - but no valid handoff survives the measured goals: after the
no-block nudges the model invents XML tool-call syntax (<tool:read>
blocks) against the text tool protocol, on every family.  This is the
same failure class the 0.4.3 session found (<tool_code> YAML), now on
multi-turn goals, and it is the proxy's non-native-tool limitation,
not an engine defect: the engine prefers native tool calling whenever
the capability probe finds it.  326 tests (8 new); engine gates
green; mock dry-run unchanged (blocked: 0).

## Session: reference-model benchmark + the three interop findings (0.4.3)

The remaining PRD benchmark gap was the reference-model numbers.  The
dev sandbox has the z-ai SDK configured, so glm-4-plus became the
reference model through a local OpenAI-compatible SSE proxy
(scripts/zai_openai_proxy.mjs: message mapping, schema-to-prompt,
429 backoff, serialized throttling) plus a "zai" provider preset.

Getting honest numbers out of it surfaced three real findings:

1. **The capability probe under-reported json_schema for fenced-JSON
   models.**  It stripped backticks with strip("`"), which cannot
   handle ```json fences; the planner parses with strip_code_fences,
   so the probe now does too.  glm-4-plus was wrongly reported
   json_schema=false (found by the benchmark: the planner then ran
   without the schema and produced 97/100 invalid plans - that first
   run also hit 102 upstream 429s and was discarded as measuring the
   rate limiter, not the model).
2. **Frontier models invent their own tool-call syntax against the
   text tool protocol.**  glm-4-plus emitted <tool_code> blocks with
   YAML arguments; the protocol prompt now shows the exact one-line
   JSON format with examples and states anything else is read as the
   final message.  Single-goal live validation: 3/3 tasks, zero
   repairs.
3. **Reference-model worker failures are model-behavior, not engine
   bugs**: H-01 (no handoff block on intermediate turns), H-03
   (constraints paraphrased instead of copied verbatim), H-06 (facts
   invented without sources - e.g. market figures hallucinated under
   a no-web config; the engine is right to reject them, that is the
   anti-hallucination contract doing its job).

**3b planner pilot (enum-fix validation)**: qwen2.5:3b with the wire
schema declaring role/type enums produces VALIDATING plans again -
2/5 goals first-attempt, 3/5 after repair; every remaining failure is
model-capability (V-01 truncation at max_tokens x2, V-11 hallucinated
tools x2), ZERO enum/schema errors.  The pre-fix failure mode is gone
(bench/results/pilot-planner-qwen2.5-3b.json).

**Packaging fixes from the deepest non-privileged install check**
(scripts/deep_install_verify.py): the deb now ships md5sums (dpkg -V
can verify installed payloads) and root/root ownership instead of the
CI user's uid.  privileged install itself (sudo dpkg -i / FUSE
AppImage) still needs a target machine: the sandbox has no
passwordless sudo and no /dev/fuse (physically verified).

**Reference-model numbers (glm-4-plus via the z-ai proxy - COMPLETE,
the run finished after the earlier partial report)**: corpus 30/30 -
plans valid 20/30 first attempt (67%, PRD reference target >= 90%),
26/30 after repair (87%, target >= 99%); completed without help 0/30
(target >= 85%) - every failure is HANDOFF-INVALID (H-01 missing
handoff block / H-03 paraphrased constraints / H-06 invented
unsourced facts under the no-web config), i.e. model behavior against
the strict handoff contract, with the engine behaving as designed.
Kill/resume 0/3 - all three cycles died on upstream worker handoff
failures before resume mechanics were exercised (the mock and the
earlier 1.5b straight-run verified the resume machinery itself).
Zero 429 errors in the final run (the proxy backoff chain absorbed
13).  Full record: bench/results/bench-real-20261122T151719Z.json.
The honest reading: the PRD acceptance targets are calibrated against
the scripted mock; no real model measured to date (glm-4-plus,
qwen2.5:3b/1.5b) meets them, and the 85% completed-without-help bar
is unreachable under the no-web corpus config.  That gap is the
product's next design question (web-gated research tasks or an
honest blocked/partial handoff pattern), not an engine defect.

What genuinely remains for a human with the target machine:
`sudo dpkg -i` / the FUSE-mounted AppImage launch (physically
impossible in this sandbox: no passwordless sudo, no /dev/fuse -
verified), and the official PRD reference-model run (whatever model
the PRD calibrates against - the pipeline is in the repo: proxy +
zai preset, or any OpenAI-compatible endpoint).

## Session: reproducible artifacts + execution smoke (after dab50be)

Both desktop artifacts now build from ANY fresh checkout and are
verified including EXECUTION (the GUI main loop really runs):

- `make_deb.py` is self-contained: it scaffolds the whole payload
  tree (control with Depends derived from the binary - ldd for the
  linked set, the dlopen'd GUI runtime appended:
  libx11-6, libxcursor1, libxrandr2, libxi6, libxkbcommon0,
  libxkbcommon-x11-0, libxcb-xkb1, libegl1, libvulkan1 - plus
  desktop entry, stdlib-generated icon, copyright) and verifies
  structurally (dpkg-deb -x + checksums), by metadata (dpkg-deb -I)
  and by execution: the unpacked binary runs 3s on a private Xvfb
  display with software EGL (llvmpipe) and opens studio.db.
- `make_appimage.py` is likewise self-contained (AppRun, desktop
  entry, icon, AppStream metainfo) and verifies by self-extraction
  + checksum + the same execution smoke on the extracted payload.
- The smoke surfaced and fixed a real gap: wgpu needs EGL and
  xkbcommon-x11 needs libxcb-xkb, none of which this sandbox had -
  resolved by extracting the distro packages
  (libxkbcommon-x11-0, libxcb-xkb1, libegl1, libegl-mesa0,
  libglx-mesa0) and pointing the smoke at them via
  LAS_SMOKE_LIBDIR; the deb's Depends list now carries the whole
  GUI runtime set.
- 8 compiler warnings cleaned (dead bindings/const, needless mut,
  egui_kittest moved to dev-dependencies, unused workspace
  rust-version); the workspace builds warning-free.
- Stale one-level-nesting claims fixed in ADR-0002 and
  ARCHITECTURE.md after audit item 14.

Artifacts: dist/local-agent-studio_0.4.0_amd64.deb (8.6 MB),
dist/local-agent-studio-0.4.0-x86_64.AppImage (12 MB).  Test
totals: Rust 141 green, Python 317 passed / 22 skipped.

Post-push follow-up: CI caught the MCP-schema e2e test being
packet-split flaky (the request-capture thread could cut the body
before the last TCP segment arrived).  Rewritten in the providers.rs
mock pattern (line-oriented headers + exact Content-Length body
read); 10 consecutive runs green, workspace 141 green.

## Session: CI-produced, download-back-verified artifacts

CI now builds, verifies and uploads both desktop artifacts on
every push (`artifacts` job), and the artifacts are re-verified on
THIS machine (Debian 13, glibc 2.41) after download - what
GitHub Actions uploaded is what actually runs elsewhere:

- Run 50 (ubuntu-latest) was green in CI, but the download-back
  loop caught the binary requiring GLIBC_2.43 - unstartable on
  Debian 13 and Ubuntu 24.04 LTS alike.  The job is pinned to
  ubuntu-22.04 (glibc 2.35 floor; move to an ubuntu:22.04
  container when the image retires, don't bump the floor).
- Run 51 exposed Depends bugs: dpkg -S queried by short name
  matched libc6-i386 (wrong-arch multilib); the versioned floors
  never fired because objdump prints versions parenthesized AND
  the prefix slice kept the underscore.  Fixed; the deb now ships
  `libgcc-s1 (>= 4.2.0), libc6 (>= 2.35), ...`.
- Run 52 exposed the mirror bug: full-path dpkg -S queries match
  nothing on ubuntu-22.04's /lib-recorded database.  Resolution
  now tries both usrmerge spellings, then the short name filtered
  to x86_64 paths, and the objdump-derived floors are included
  unconditionally.
- AppStream: the component id is reverse-DNS
  (io.github.kimpearce888.local-agent-studio), the desktop files
  and metainfo filename follow it; appstreamcli (present on
  runners, absent in this sandbox) validates during the build.
- Run 53's artifacts, re-verified here: deb unpack + control
  fields + payload + 3s Xvfb GUI smoke; AppImage self-extraction
  + payload + same smoke.  Both PASS; sha256 in the run log.
- scripts/verify_ci_artifacts.py (in-repo) automates the
  download-back check for any run id.

## Session: real-server capability tests + real-model benchmark + rootless install verification

A user-mode ollama server (no root: tarball under /tmp, OLLAMA_MODELS
on /tmp) stands in this sandbox on 127.0.0.1:11434 with qwen2.5
0.5b/1.5b/3b.  That resolves the three long-blocked audit items as
far as physics allows:

- **Real-server capability tests (#19): DONE.**
  `detect_servers()` finds port 11434, guesses ollama, auth none -
  the setup-wizard detection path against a real server.  The
  capability probe against the live server: reachable/authenticated/
  models_listed TRUE, streaming TRUE (real SSE), tool_calling TRUE
  (the model actually called the probe's ping tool), json_schema
  TRUE (ollama enforced response_format json_schema and the model
  produced {"ok": true}); token_counting and context_length honestly
  FALSE (ollama exposes neither).  `python -m bench --provider
  ollama --model ...` runs this probe first and lets its findings
  drive the planner configuration - the app's own setup flow,
  exercised for real.
- **Real-model benchmark (#20): MEASURED, with an honest finding.**
  All three local models FAIL the planner phase after every repair
  round (0.5b/1.5b: V-03 broken task cross-references; 3b: V-01
  invented role values; 3b takes ~11.5 min per goal on 2 CPU cores).
  CPU-scale models cannot produce valid plans for the product's plan
  schema - that is a real model-capability measurement, not a
  harness gap (the harness is mock-validated).  run_worker_real
  decomposes the phases: scripted valid plan + live WORKER measures
  handoff writing, task outcomes and kill/resume.  The OFFICIAL
  run (8 goals, 2 kills, qwen2.5:1.5b on this 2-core sandbox,
  ~7.5 tok/s): corpus 0/8 valid first attempt, 0/8 after repair
  (mean 3.0 repair rounds, every goal died in planning - V-03
  broken cross-references), kills 0/2 (both plan-invalid before any
  run existed), engine gates PASSED.  Result file:
  bench/results/bench-real-20261122T113803Z.json.  The worker-phase
  measurement started but was killed by the session budget
  mid-run (exit 137) - its function is committed and mock-validated;
  re-run with `bash /home/z/my-project/scripts/run_real_bench.sh`.
  PRD reference-model numbers still require the external reference
  endpoint; what is now PROVEN is that the whole pipeline (probe ->
  planner -> repair loop -> kill/resume) drives a real server and
  measures real model behavior end to end.
- **Privileged install steps: verified as far as rootless allows.**
  `dpkg --root=<tree> --force-not-root -i` - a REAL dpkg install:
  unpack, database registration (iU), versioned-Depends enforcement
  (dpkg itself reports the libc6/libgcc-s1 floors absent from the
  fake root), and the deb has NO maintainer scripts, so unpack is
  the complete install action.  The binary AS INSTALLED BY DPKG runs
  the 3s Xvfb GUI smoke and opens its store.  desktop-file-validate:
  PASS on the installed entry.  The AppImage launches through its
  own runtime with --appimage-extract-and-run (the documented
  no-FUSE path; /dev/fuse does not exist here) and passes the same
  smoke.  The only step left unperformed is literally `sudo dpkg -i`
  on a real desktop and a FUSE mount - both need what this sandbox
  physically lacks.

## Session: v0.4.1 - the benchmark session's fixes shipped as a release

- **The real-model benchmark found a production defect and it is
  FIXED**: the planner's wire schema declared role/type as bare
  strings while the validator enforces ROLES/TASK_TYPES - measured
  as 'qwen2.5:3b produced well-formed tasks and failed ONLY on
  invented roles'.  The schema now declares both enums; any
  json_schema-capable server grammar-rejects invalid values at
  generation time.  317 tests green; GUI suite green under Xvfb.
- **v0.4.1 released**: versions unified (pyproject, las.__version__,
  Cargo workspace, artifact filename checks), the settings UI test
  now asserts the version from CARGO_PKG_VERSION instead of a
  hardcoded number.  CI run 56 green; the .deb and AppImage
  download-back verified on this machine (execution smokes OK,
  sha256 recorded in the release policy trail) and copied to
  download/.
- **verify_ci_artifacts.py hardened**: with an empty
  LAS_SMOKE_LIBDIR the smoke crashed on `Path("").parents[1]` (Path
  of "" resolves to cwd, which exists) instead of running plain -
  fixed to require a non-empty value.
- **Worker-phase measurement: found, root-caused, FIXED and
  verified.**  The live-worker run was OOM-killed three times
  (~1.86-3.2GB rss inside a single goal) while every attempt cap
  held.  A deterministic in-process repro (scripts/repro_worker_oom.py:
  a rambling adapter, no HTTP, no model server) reproduced the kill
  in seconds and bisected it to ONE line: step() returned the raw
  run status "failed" as the result kind, which run()'s break list
  does not contain (it expects "run_failed") - so run() over an
  already-failed run spun forever appending StepResults until the
  OOM killer took the process.  The kill/resume benchmark hits
  exactly this path whenever the live model fails a task before
  the kill.  Fixed in driver.py (status->kind mapping); regression
  test added (tests/test_runstate.py, TestRunOnFailedRun); the
  repro completes in 0.0s at 26MB flat where it previously OOM'd
  at 3.2GB.  Full suite: 318 OK.  CONFIRMED against the live model
  after the fix: the exact kill-mode measurement that OOM'd three
  times now completes cleanly
  (bench/results/worker-real-kill-qwen2.5-1.5b-0.4.2.json - the
  rambling 1.5b fails the first task and the resume driver returns
  immediately; honest numbers, no hang).

## Exact next engineering action

None blocking on the engine.  v0.4.4 shipped the honest-blocker
machinery, the benchmark input provisioning and the two verbatim-copy
fixes; the open item is a MODEL-side measurement, not engine work:

- rerun the reference benchmark against a provider with NATIVE tool
  calling (any OpenAI-compatible server with tools - the engine
  already prefers native tools when the probe finds them).  The z-ai
  proxy's text tool protocol is the last wall: glm-4-plus, after the
  no-block nudges, invents XML tool syntax (<tool:read>) on
  multi-turn research goals.  Everything else in the loop is now
  measured working: workspace fixtures are read, file-sourced facts
  validate H-06, and a live model produced a fully valid four-field
  blocked handoff during diagnosis.

The P0 scope, the recorded implementable P1 list and ALL audit
residuals are built, tested and packaged; the three
long-environment-blocked items were resolved as far as this
sandbox's physics allow:

- real-server capability tests (audit 19): DONE against a live
  user-mode ollama server (see the session section above);
- real-model benchmark (audit 20): the official run measured
  (bench-real-20261122T113803Z.json), the plan wire schema now
  declares role/type enums (the fix the benchmark surfaced), and
  the worker-phase decomposition + the enum-fixed model retest are
  the measurements in flight this session;
- privileged install: verified rootless end to end (dpkg --root,
  installed-binary smoke, desktop-file-validate, AppImage
  --appimage-extract-and-run).

What genuinely remains for a human with the target machine:
`sudo dpkg -i` / the FUSE-mounted AppImage launch (root + /dev/fuse,
which this sandbox physically lacks), and the PRD reference-model
benchmark numbers (they need the external reference endpoint; the
pipeline that will produce them is now proven end to end against a
real server, and the OOM bug that masked the worker-phase numbers
is fixed).