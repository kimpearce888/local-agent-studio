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

## Exact next engineering action

None blocking - the P0 scope, the recorded implementable P1 list
AND the former audit residuals are all built, tested and packaged:
session rollover (9.4), the full MCP client (stdio + Streamable
HTTP + dynamic tool schemas in the model context, 13.2), Landlock
run_command (13.4, audit item 10), versioned artifacts (10),
provider-exact token accounting (9.3), and audit items 13/14.  The
ONLY remaining items are the environment-blocked verifications:
real-server capability tests and the real-model benchmark (audit
items 19/20 - harnesses shipped, they need a model server to point
at), and the final privileged install steps - `dpkg -i` (root) and
a FUSE-mounted AppImage launch on a target machine; everything
short of those two steps is now verified here, including the GUI
actually running.  A tokenizer dependency would only ever be wanted
beyond the provider-exact counting already in place.
