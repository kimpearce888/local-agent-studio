# Changelog

All notable engine changes. Format follows Keep a Changelog; versions
follow semantic versioning. Documentation, this changelog and migration
notes are release gates under the [release policy](docs/user/release-policy.md),
not follow-ups.

## [Unreleased]

### Fixed

- **The CI run flake (runs 33/35/38/39)**: the Python store shared
  one sqlite3 connection between the loopback API threads and the
  run-coordinator thread without serialization
  (`check_same_thread=False` only disables the thread check).
  A poll-side SELECT iterating while the run thread executed could
  hand the reader a zero-column Row and crash the run thread
  (`IndexError` in `events()`, captured by the crash-hardened
  coordinator as `RUN-THREAD-CRASHED` with a full stack).  Every
  public Store method is now serialized through a reentrant lock;
  the new stress test fails without it and is green with it.

### Added

- **Automatic session rollover (mandate 9.4)**: one logical
  conversation now spans multiple session segments.  When a
  transcript outgrows the agent's policy
  (`rollover_max_messages` / `rollover_max_tokens` in profile
  params; defaults 200 messages / 24 576 estimated tokens) the
  runtime rolls over BEFORE the next turn instead of silently
  dropping the oldest history: the prior segment stays intact with
  a closing note, the next segment opens with a structured
  continuity summary (pinned instructions and unresolved
  requirements preserved verbatim, decisions, references and
  artifacts carried over, recent tail), and both are inspectable in
  the desktop's session combo (`segment_of` chains the lineage).
  `restore_segment` resumes the logical conversation from an
  earlier segment - history is append-only, so restoring branches a
  new segment seeded from the earlier one instead of rewriting it.
- **MCP client, stdio transport (mandate 13.2)**: configured Model
  Context Protocol servers run as child processes speaking
  newline-delimited JSON-RPC 2.0 - initialize handshake,
  tools/resources/prompts listing, tools/call with isError
  handling, 5 s/10 s timeouts, respawn after a server crash
  (audited).  Security posture: connection requires explicit user
  enablement (imported configs stay disabled - approval BEFORE
  connection), per-server tool allowlists fail closed, per-agent
  grants use `mcp:<server>` (whole server) or
  `mcp:<server>:<tool>` through the same broker (ask-modes route
  through the approval gate), every result is labelled UNTRUSTED
  content, and connect/restart/call outcomes are audited.
- **MCP Streamable HTTP transport**: configs with a `url` connect
  over HTTP instead of spawning a process - initialize handshake
  with `Mcp-Session-Id` capture (the session header is sent on
  every later request; the test server refuses without it),
  `application/json` and `text/event-stream` (SSE) response
  parsing, 202-accepted notifications, per-call error marking with
  reconnect on next use.  `url` and `command` are mutually
  exclusive, validated at upsert.  Not yet: dynamic MCP tool-schema
  injection into model turns.
- **Artifacts as versioned first-class entities (mandate 10)**: an
  `artifacts` registry with logical ids and monotonically allocated
  versions, sha256 hashes, agent ownership, JSON provenance,
  optional project sharing, capped previews, and deletion (the
  reference goes; the owner's workspace file stays).  Sharing never
  bypasses the jail: `export_artifact` COPIES verified bytes
  (hash-checked against registration) into the recipient's own
  workspace.  The Artifacts screen now lists registered artifacts
  with owner/project/version/size/hash, previews them, and
  registers/deletes via real buttons (kittest-verified).
- **Landlock-sandboxed run_command (mandate 13.4)**: a new broker
  tool executing explicit ARGUMENT ARRAYS (never shell strings)
  inside a Landlock sandbox - the agent's workspace jail is the
  only writable root, system directories are read/execute, applied
  via raw syscalls in pre_exec with PR_SET_NO_NEW_PRIVS.  Limits:
  wall-clock timeout with process-tree cancellation (setsid +
  group SIGKILL), CPU/address-space/file-size/no-file rlimits,
  64 KB output cap.  Command policy from profile params
  (`command_allowlist`, fail-closed when empty).  FAILS CLOSED on
  kernels without Landlock (this build sandbox's 5.10 kernel
  exercises exactly that path; CI's 6.x kernels exercise the real
  confinement: in-jail writes succeed, escapes and outside reads
  are refused, timeouts kill the tree).
- **Dynamic MCP tool schemas in the model context (mandate 13.2)**:
  an agent's granted-and-enabled MCP servers now inject their tool
  names, descriptions and input schemas into the turn's system
  context, with the exact TOOL_CALL wire format and an UNTRUSTED
  label (schemas describe inputs; they are never instructions).
  The catalog applies all three filters before anything enters the
  context: server enablement (unavailable servers surface as honest
  per-server reasons), the server's own tool allowlist, and the
  agent's grants (an exact `mcp:<server>:<tool>` grant lists only
  that tool).  Capped at 20 tools / 400 chars of schema per tool;
  empty when nothing is granted.  Verified end to end by capturing
  the real request body sent to the provider.
- **Credentials serializer audit closed (audit item 13)**: test-proven
  that the resolved key never materializes anywhere - error paths
  (network failures, HTTP 401) carry URLs and statuses, never the
  Authorization value; the endpoints table, its API rows and their
  JSON serialization carry the env var NAME only; a raw scan of the
  database file finds no secret bytes.  The adapter struct
  deliberately carries no Debug/Serialize impls.
- **Python module registry: recursive nesting (audit item 14)**:
  modules may nest at any depth under `las/modules/` (discovery
  walks the whole tree); the one-level limit is gone.  Structure
  rules are now: parent must exist, and the parent CHAIN must be
  acyclic (walked with a seen-set, self-parent included) and
  shallower than 64 levels as a sanity guard.
- **Provider-exact token accounting (mandate 9.3)**: every model
  call's EXACT provider token counts (usage.prompt_tokens /
  completion_tokens) are persisted in `token_usage` next to the
  pre-call chars/4 estimate of the same request, and
  `context::calibrate` uses the latest observation to shrink the
  context budget when the provider counts more than the estimate
  (and labels the basis - conservative, confirmed-safe, or
  calibrated - never pretending an estimate is exact).  Verified
  end to end against a real local HTTP endpoint returning usage.

## [0.4.0] - 2026-11-21

### Added

- **The Rust product engine** (`rust/crates/las-core`): the
  agent-first domain - persistent agents with versioned profiles,
  sessions and canonical transcripts, durable versioned memory,
  inboxes with delivery states and idempotency, channels and a
  shared blackboard, default-deny agent-to-agent topology, the full
  communication set (messages, calls, delegation with cycle/depth
  protection, control transfer), a tokio actor runtime with
  per-session turn locks and restart recovery, a fair shared
  inference scheduler, token-budgeted context assembly with memory
  and reply routing, a permission broker with workspace-jail file
  tools, hardened web fetch (SSRF resolved-IP checks, per-hop
  re-validated redirects, MIME gate), the TOOL_CALL loop with the
  ask-once/ask-each approval gate, and SQLite WAL storage that
  reads the Python engine's event-log database alongside.
- **The egui desktop shell** (`rust/crates/las-desktop`): the
  mandate's fourteen navigation areas, all live on real las-core
  data, verified headlessly with egui_kittest.
- **Packaging**: `.deb` and AppImage built and structurally
  verified by `scripts/make_deb.py` / `scripts/make_appimage.py`;
  versions unified at 0.4.0; CI gates the Python matrix and the
  full Rust workspace (tests + release build).

## [0.3.0] - 2026-11-21

### Added

- **The desktop app (M2): all thirteen screens of the specification,
  pure tkinter, zero new dependencies.** `studio gui` opens the shell
  with the five-entry navigation, the top bar (goal title, status chip,
  provider health, budget meter, command palette), first-launch setup
  wizard with server detection and the capped self-test, the run
  history with filters/sorting/pagination and row actions, the goal
  composer with templates and round-by-round planner progress and
  cancel, the Studio (canvas with sequence/data edges, drag reorder
  with dependency refusal, port-to-port links, the accessible list
  view, the eight-tab inspector, the Constraints/Lint/Log dock,
  preflight, undo/redo, autosave), context preview, the live run view
  (task rail, transcript with UNTRUSTED labelling, handoff and files
  tabs, pause/cancel/resume controls, budget meter, completion card),
  the approval-gate and failure dialogs with every spec action, the
  providers screen with capability test table, the library, the
  seven-section settings with a shortcut editor, export and redacted
  diagnostics dialogs.  Light, dark and high-contrast themes; text
  scale 100-200 percent; every status icon+text; F6 region cycling;
  dialog focus trapping; the keyboard map of spec section 9.
- **Network log (S11 Privacy)**: every connection the GUI makes flows
  through a logging proxy on the provider registry and is shown in
  Settings → Privacy.
- **GUI tests (22) under Xvfb**: all screens instantiate, the AC-11
  keyboard flow (reorder, add dependency, delete with dependents
  warning), canvas/list parity, WCAG contrast checks of all three
  palettes, non-blank render checks, first-launch wizard path.

### Changed

- `Planner.plan` accepts `cancel_check` and `on_reply` callbacks
  (backward compatible) so the GUI can cancel between rounds and show
  each round's reply.
- The run-summary cache gained `started`, `duration_s`,
  `planner_model` and `worker_model` columns for the history table;
  legacy caches migrate by ALTER + re-derive (the table is a derived
  cache, no data migration is needed).
- `Engine.create_run` records the planner/worker models in the
  `run.created` event.

### Fixed

- **GUI development found and fixed a real engine-side bug**: the
  Studio's `add_dependency` had its direction inverted (the provider
  task received the dependency instead of the consumer); tests pin the
  forward-only rule.

## [0.2.1] - 2026-11-21

### Fixed

- **The driver replays state incrementally (RUN-5).** Each task refetched
  run state by replaying the whole event log (twice: once for the plan,
  once for the replay), making a run O(n^2) in tasks - a 500-task run
  (the PRD scale target) took 23 seconds of pure overhead. `replay()`
  now accepts a previous state and an after-seq cursor, `RunState`
  carries the cursor, and the driver resumes from it: the same run
  completes in 1.7 s, 13.6x faster, with an equivalence test pinning
  incremental == full replay.
- **Run ids widened from 24 to 48 bits.** At the PRD's 10,000-run
  history target, 24-bit suffixes birthday-collide (about 3 expected
  collisions per 10,000 runs, confirmed by a failing load test); two
  runs sharing an id silently merge. 48 bits put 10k-run collision odds
  at ~2e-7.

### Added

- **Scale load tests (PRD section 11, Scale):** a 500-task run at the
  MAX_TASKS_HARD cap, a 10,000-run history listed through the summary
  cache (cold 0.64 s, warm 0.10 s), and a ~96 MB transcript read through
  the after-seq cursor with the paging contract asserted.
- **Unicode end-to-end tests (Internationalization):** CJK, right-to-left
  Arabic, accented Latin and emoji goals survive planning, constraint
  extraction, handoffs, events and both export formats byte-exactly.
- **Migration test from v0.1.0 (Maintainability):** a legacy data
  directory without the run_summary table opens on current code, the
  cache is recreated transparently (CREATE TABLE IF NOT EXISTS - no
  version bump, no dump and restore), and old and new runs list
  together.
- **Benchmark at the full PRD sample:** 1,000 forced-kill/resume cycles,
  all resumed cleanly (results in bench/results/).

## [0.2.0] - 2026-11-21

### Fixed

- **Approval gates are no longer stripped from planner drafts.** A task
  with `gate_before: true` in the model's reply kept the flag silently
  dropped by draft conversion, so a planned approval gate could never
  reach a run. The field now survives planning and the repair loop, is
  advertised in the structured-output JSON schema and the plain-text
  schema, and four regression tests pin it (draft, repair path, schema,
  and L-01 clearing at lint time). Gate loss is trust-boundary loss:
  this entry is the reason the 0.2.0 bump exists.

### Added

- **Phase 0 benchmark package (`python -m bench`, PRD section 17).** A
  deterministic 100-goal corpus (20 scenario families x 5 variants:
  88 clean, 8 repair, 4 approval-gated) and a harness that measures every
  engine-deterministic PRD metric: plans valid on first attempt / after
  repair, runs completing without human help, resume after forced kill
  (abandon-at-random-step fault injection), cap enforcement terminal with
  zero work after the cap, and a socket audit proving zero outbound
  connections in the default configuration. The mock dry-run validates
  the machinery (92/100, 100/100, 96/100, 200/200 kills, zero
  violations, results in `bench/results/`); real numbers need the
  reference model and a reachable server.

### Changed

- **S5b: the host CLI is now a composition root.** Command handlers
  moved verbatim into `las/app/commands/` (a shared context in
  `common.py`; six domain modules), and `las/app/cli.py` shrank from
  1221 to 279 lines of parser + dispatch + backward-compatible
  re-exports. No public behavior changed.
- **`studio list` reads a derived summary table.** A `run_summary` cache
  (refreshed only when a run has new events, staleness checked by event
  seq) replaces per-listing full replays: warm listings measured at
  0.2 ms versus 5 ms for replay-everything on a 20-run history.
  `delete_run` drops the row; the cache always matches a replay.

### Migration notes

- Python API: the `las.app.cli` import surface (`main`, `build_parser`,
  `EXIT_*`) is unchanged; handlers now live in `las.app.commands.*`.
- Storage: `run_summary` is created by `CREATE TABLE IF NOT EXISTS` on
  open - existing data directories upgrade transparently, no dump and
  restore needed (the engine writes `<data>/backups/` before schema
  changes regardless).

## [0.1.0] - 2026-11-21

Initial engine: event-sourced runs with hash-chained logs (tamper
evidence), the six-contract kernel, twelve loadable modules behind a
manifest registry with capability gates, planner with constraint
extraction and a repair loop, the rule-of-two permission broker with
tool allowlists and workspace jail, run caps, approval gates,
fault-tolerant resume, re-planning, run comparison, attachments (MD /
TXT / CSV / JSON), reporting and export, opt-in OTLP telemetry, the
studio CLI with JSON modes, the local API server, the self-test, an
executable architecture checker (R1-R8) and the removal tests. 281
tests at release.
