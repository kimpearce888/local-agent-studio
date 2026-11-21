# Changelog

All notable engine changes. Format follows Keep a Changelog; versions
follow semantic versioning. Documentation, this changelog and migration
notes are release gates under the [release policy](docs/user/release-policy.md),
not follow-ups.

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
