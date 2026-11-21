# Progress and Handover Log

Running record of architecture migration and feature development. Newest entry last.
Verification states used per the Global AI Agent Development Standard:
PASSED / FAILED / BLOCKED / SKIPPED / NOT RUN — never inflated.

## Baseline (2026-11-21, commit 6a754ad)

- M0 engine core complete: schemas + V/H validation, hash-chained event log, mock
  provider, Chat Completions + Responses adapters, context assembly with budget and
  compaction, planner with repair loop + critique, linter L-01..L-09, workspace jail +
  broker + Rule of Two, sequential scheduler with gates/failures/caps/controls/rerun,
  export, CLI (plan/run/list/inspect/resume/approve/reject/export/providers/selftest/
  verify), self-test, message catalogue.
- Verification: `python3 -m unittest discover -s tests` — PASSED, 181 tests.
- Audit findings F-01..F-11 recorded in the architecture assessment
  (see `download/LAS_Architecture_Assessment_and_Plan.docx`, mirrored summary in
  ARCHITECTURE.md / DEPENDENCY_RULES.md).

## S0 — Documentation baseline (this commit)

- Added `ARCHITECTURE.md`, `MODULE_MAP.md`, `DEPENDENCY_RULES.md`, `AGENTS.md`,
  `docs/adr/0001..0004`, this log. No code changes.
- Gate: full suite green (unchanged), docs describe current reality + approved target.
- Environment facts for this development sandbox: Python 3.12.16 only (3.10/3.11
  NOT RUN here — CI matrix closes the gap); Node 24 present; Rust/cargo ABSENT, so
  the M2 Tauri shell cannot be compiled or tested in this environment — engine-side
  work proceeds, shell work is reported as blocked, not silently dropped.

## S1 — Shared kernel extraction (ADR-0001)

- Created `las/shared/`: `plan_contract` (plan v1 types/parsing/variables — validation
  stays in `las.schema.plan` until S4), `provider_contract` (Adapter port, message
  model, `ProviderProfile`, `SimulatedCrash`), `events_contract` (Event, EVENT_TYPES),
  `messages` (moved). Old paths re-export the kernel (shims).
- Fixed F-01: providers no longer import storage (`ProviderProfile` now lives in the
  kernel). Fixed F-02: the engine no longer imports the mock provider
  (`SimulatedCrash` is a kernel fault-injection signal; `selftest` keeps its declared
  optional dependency on the mock).
- Gate: `python3 -m unittest discover -s tests` — PASSED, 181 tests, zero test edits.

## S2 — Registry, manifests and the pilot (ADR-0002)

- Host: `las/app/` with `registry.py` (pkgutil manifest discovery, validation,
  acyclicity, enablement resolution, `load()` as the authoritative enablement
  boundary) and `capabilities.py` (enable/disable policy; core and
  required-dependent protection; optional dependents degrade).
- Kernel: `shared/module_contract.py` (ModuleManifest, CommandSpec).
- Registered modules (4): `providers` (core, parent), `providers.mock` (child,
  independently disableable/removable), `reporting`, `selftest` (optional dep on
  `providers.mock`). Providers package moved to `las/modules/providers/`
  (deviation from the assessment's S2 scope: the child module needs its parent
  package in place, so the providers move was absorbed here; S4 is smaller).
- Providers facade no longer re-exports the mock: `build_adapter` imports the
  child on demand and fails cleanly when it is absent.
- CLI: `studio modules list|enable|disable`; capability gates on `export`
  (reporting), `selftest` (selftest + mock), and any mock provider profile
  (`_registry_for`).
- Tests: `tests/test_registry.py` (discovery completeness, uniqueness, parents,
  deps, cycles, contract importability, MODULE_MAP sync, CLI command
  registration), `tests/test_module_lifecycle.py` (disabled-state: blocked
  commands with actionable fixes, failure isolation, data retention,
  re-enable, core protection), `tests/test_reporting.py` (reporting-owned,
  with a local contract-compatible scripted adapter — also a replacement
  proof: the engine runs end-to-end against a non-mock adapter),
  `tests/test_selftest.py` (selftest-owned).
- `tools/removal_test.py`: disposable-copy removal harness (module dir + declared
  test files + compat shims + transitive test deletion, registry re-scan, stale
  reference scan with documented `# optional-dep:` degradation markers,
  remaining-suite run). Issues found and fixed by the removal tests:
  1. selftest had a hard top-level import of the optional mock (now deferred
     with graceful degradation + marker);
  2. test files depended on other modules' commands/fixtures without declaring
     it (selftest/export assertions moved to module-owned files; reporting
     tests now own their fixtures instead of using tests.util/mock);
  3. registry validation rejected absent *optional* dependencies (now: required
     must exist, optional may be absent by design).
- Verification: `python3 -m unittest discover -s tests` — PASSED (212 tests).
  Removal tests — PASSED for `reporting`, `providers.mock`, `selftest`
  (disposable copies; working tree untouched).

## S4 — Mechanical migration of the remaining modules (ADR-0004)

- Moved into `las/modules/`: planning, planquality (validation + linting),
  handoffs, execution (scheduler + runstate), context, security (tools +
  workspace), storage; the CLI moved to `las/app/cli.py`. Entry point is now
  `studio = las.app.cli:main`. Nine new manifests with declared dependencies
  (validated acyclic by the registry test); pilot manifests updated to their
  real dependencies (reporting→execution, selftest→execution+providers).
- Shared kernel gained `tool_catalog.py` (tool definitions and Rule-of-Two
  flags as a contract used by planning, planquality and security).
- Import rewrites: every file under `las/modules` and `las/app` is fully
  canonical; the architecture checker's legacy exception list is now EMPTY
  (R1-R8 fully enforced in the canonical zone). Legacy paths survive only as
  pure re-export shims (declared per manifest in `compat_shims`) for the
  untouched test suite.
- Test infrastructure decoupling forced by the removal tests (documented
  deviation from "zero test edits" at S4: imports only, no assertions):
  `tests/planlib.py` (mock-free helpers) split from `tests/util.py`
  (mock-driven engine harness); mock-fixture test classes carry explicit
  `skipUnless` so any mock-less build stays green; reporting and selftest
  own their test files. The harness now matches test dependencies by AST
  imports (not text), handles transitive fixture deletion, and adjusts
  surviving manifests when fixture-coupled test files go (what a mock-less
  build would ship).
- Verification: `python3 -m unittest discover -s tests` — PASSED (236
  tests, zero assertion changes); removal tests — PASSED for `reporting`,
  `providers.mock`, `selftest` (working tree untouched); `python3 -m las
  selftest` against the mock — PASSED end to end.

## S5 — God-module split (scheduler done; CLI split evaluated and deferred)

- S5a (committed 963d393): the 851-line scheduler became cohesive private
  files inside the execution module — attempt.py (334: one attempt, tools,
  caps, loops, handoff validation), driver.py (363: lifecycle and task
  loop), gates.py, controls.py, budgets.py, engine.py — with the facade and
  public API unchanged. Injection seams added (DriverConfig.workspace_factory
  / broker_factory) for replacement testing (fixes F-07's engine half and
  F-10).
- S5b (deferred, decision recorded): moving the CLI command handlers into
  module-owned cli.py files was implemented, evaluated and rolled back —
  the handler/helper coupling (capability gates, provider-registry helper)
  requires a context-object redesign that risks user-facing regressions
  disproportionate to the remaining benefit. The host CLI stays the
  composition root; documented as a size-fitness exception in
  tests/test_architecture.py with the M2 milestone as the review point.
- Verification: 221 tests PASSED, all three removal tests PASSED,
  `python -m las selftest` PASSED.

## S6 — CI and push (committed 39fde07)

- .github/workflows/ci.yml: Python 3.10/3.11/3.12 matrix running the full
  suite (architecture + registry checks included), the three disposable
  removal tests, and a packaging smoke test (pip wheel + install +
  studio --version).
- R9 size fitness added to the architecture checker (450-line limit for
  module files; one documented exception: the host cli).
- Pushed to GitHub (main, 39fde07 and later). CI outcome NOT VERIFIED from
  this sandbox (the gh CLI is unavailable); the identical commands were run
  locally and passed.

## S7 — Shim removal: NOT DONE (remaining work)

Legacy re-export shims (declared per manifest in compat_shims) and legacy
test imports are still in place by design of the transition; removing them
is mechanical follow-up work (delete shims, rewrite test imports with the
S4 mapping, empty the manifests' compat_shims, update DEPENDENCY_RULES).
The architecture checker already enforces that no NEW legacy imports can
appear in the canonical zone.

## M1 — Loopback engine HTTP API (spec 7.5)

- New `api` module: 127.0.0.1-only server, per-launch token written to
  <data_dir>/api-token with mode 0600, Origin/Host checking (403), 401 on
  bad token; endpoints for health, providers (CRUD, probe, models), plans
  (generate/read/save), runs (start on a background thread — one at a
  time, RUN-2), events with ?after=seq polling, run controls, task
  controls, gate decisions, handoff editing, export, templates. Errors use
  the spec's {"error": {code, message, fix}} shape.
- `studio serve` command, registered through the module registry; the
  api module declares reporting as an optional dependency (the export
  endpoint degrades with an actionable 409 when reporting is absent).
- Storage: sqlite3 connections now allow the API/coordinator threads
  (check_same_thread=False, WAL serializes writers).
- Defects found and fixed during verification: URL routing spread
  urlparse fields into the wrong parameters (all routes 404); SQLite
  cross-thread use; one broken intermediate commit was pushed and then
  repaired (see git history).
- Verification: full suite PASSED (230 tests) including 9 API tests
  (auth, origin, CRUD, end-to-end run against the mock with events and
  export, polling). Removal tests: reporting/providers.mock/selftest all
  PASSED after the api module landed (api holds no shim baggage).

## Remaining work (honest list)

- S7 shim removal + canonical test imports (mechanical).
- S5b CLI command split (context-object redesign).
- M1 items not feasible in this sandbox: nightly contract tests against
  real servers (no LM Studio/Ollama available); the 100-goal benchmark
  (needs a real model); derived SQL tables optimization.
- M2 desktop shell: BLOCKED in this environment (no Rust toolchain); the
  engine API it will consume is in place and tested.
- M3/M4 features (MCP client, attachments, signed installers, OTLP export,
  run comparison, replanning UI) — not started; P1 tools run_command and
  web_fetch already exist with allowlists from M0.

## Handover notes (maintained continuously)

- To run everything: `python3 -m unittest discover -s tests`;
  `python3 -m las selftest`; `python3 -m las run <plan.json> --provider mock`.
- Source of truth for requirements: `docs/prd.md`, `docs/product-spec.md`.
- Architecture rules live in `DEPENDENCY_RULES.md` and are enforced by
  `tests/test_architecture.py` + `tests/test_registry.py` once S2/S3 land.
