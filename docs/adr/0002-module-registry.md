# ADR-0002: Module registry with automatic manifest discovery

- Status: accepted
- Date: 2026-11-21
- Decision owners: engineering (architecture)
- Affected modules: all (host `las.app`, every `las/modules/*`)

## Context

M0 wiring is implicit in import statements: nothing declares which modules exist,
what they require, or what happens when one is missing (finding F-03). Module-level
enable/disable does not exist (F-04), and there is no place where contributed CLI
commands are declared. The standard requires a documented, reproducible discovery and
registration mechanism, and forbids claiming automatic discovery where an index must
be hand-edited.

## Decision

Each module ships `manifest.py` exposing a `MODULE` instance of
`shared.module_contract.ModuleManifest`: id, version, parent, summary, contract import
path, `requires` / `optional` module ids, `default_enabled`, `core` (host-required),
contributed `commands`, and `test_files`. The host (`las/app/registry.py`) discovers
manifests by a recursive `pkgutil` scan of `las/modules/**/manifest.py` at any
nesting depth — Python needs no generated references, so this is reliable
automatic discovery with no central index. The
registry validates: unique ids, parents exist, parent chains acyclic (audit item
14: trees may nest recursively; a seen-set walk rejects cycles and self-parenting,
with a 64-level sanity guard), declared dependencies exist, graph
acyclic, declared test files exist. Enablement is a settings key (`module.<id>.enabled`)
resolved at process start; disabled modules are not handed out and their commands are
not registered; required dependents of a disabled/missing module produce an
actionable error naming both modules; optional dependents degrade as declared.
`providers.mock` registers as a child module so dev/test builds can exclude it.

## Alternatives considered

- A central hand-written list in the host: rejected — it drifts; the standard's
  preferred mechanism is automatic discovery when reliable, which it is here.
- `importlib.metadata` entry points: rejected — they require installed distributions
  and solve third-party plugin loading, a problem this product does not have.
- Runtime hot-unload on disable: impossible in Python; disablement is defined as
  effective at the next process start (documented in ARCHITECTURE.md §8).

## Consequences

Benefits: dependencies become explicit and machine-checked; enable/disable/removal
get one authoritative choke point; parallel agents get a per-module ownership unit.
Costs: one manifest file per module (small dataclass) and a scan on startup
(milliseconds). The registry test must keep manifests and directories in sync — that
is exactly the anti-drift property we want.

## Verification

`tests/test_registry.py` (discovery completeness, uniqueness, dependency existence,
acyclicity, disabled-state semantics); `tests/test_architecture.py` (cross-module
imports must be declared in the importer's manifest); `tools/removal_test.py`
(delete module + its declared test files on a disposable copy, re-scan, suite green).
