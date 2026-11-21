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

## Handover notes (maintained continuously)

- To run everything: `python3 -m unittest discover -s tests`;
  `python3 -m las selftest`; `python3 -m las run <plan.json> --provider mock`.
- Source of truth for requirements: `docs/prd.md`, `docs/product-spec.md`.
- Architecture rules live in `DEPENDENCY_RULES.md` and are enforced by
  `tests/test_architecture.py` + `tests/test_registry.py` once S2/S3 land.
