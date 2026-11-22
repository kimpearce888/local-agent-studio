# Architecture Overview

> Factual and synchronized with the implementation. Where a section describes the
> migration target rather than current reality, it says so explicitly.

## 1. System summary

- Purpose: turn one large prompt into a chain of small, isolated agent tasks that run
  sequentially against local model servers, with validated handoffs, hard budgets and a
  durable, auditable event log.
- Project type: modular monolith — one Python package (`las`) exposing a library engine,
  a CLI (`studio`), and (from M1) a loopback HTTP API. A desktop shell (Tauri) is a
  future client of the same engine API.
- Runtime/deployment shape: single process, standard library only (Python >= 3.10);
  SQLite in WAL mode is the only persistence; one workspace folder per run.
- Primary technologies: Python 3 stdlib (sqlite3, urllib, json, dataclasses, argparse,
  http.server for tests and the M1 API), setuptools packaging via `pyproject.toml`.
- Supported environments: Windows/macOS/Linux, Python 3.10-3.12; fully offline.

## 2. Architectural approach

Agent-First Recursive Modular Architecture (per the Global AI Agent Development
Standard), combining:

- **Modular monolith**: one deployable; no services, no plugin binaries.
- **Recursive feature modules** (migration S1-S7, approved): host (`las.app`) +
  shared kernel (`las.shared`) + capability modules (`las.modules.*`), each with a
  manifest, public contract facade, owned tests, and safe enable/disable/removal.
  `providers.mock` is the first registered child module.
- **Hexagonal boundaries**: the provider port (`shared.provider_contract`) inverts the
  adapter layer; storage is consumed through its facade contract; the driver receives
  collaborators (Workspace, PermissionBroker, adapters) through injection seams.
- **Contract-driven development**: module facades with explicit `__all__`; manifests
  declare required/optional dependencies; cross-module imports must be declared.
- **Architecture fitness functions**: `tests/test_architecture.py` (stdlib AST checker)
  and `tests/test_registry.py` run inside the normal unittest suite and in CI; a
  disposable removal-test harness verifies module removability.
- Patterns deliberately not used: microservices, runtime-loadable plugins, a generic
  plugin framework, event bus for synchronous calls (direct contract calls only;
  the durable event log is the audit/integration record, not a message bus).

## 3. Runtime and build overview

- Entry points: `studio` console script and `python -m las` → `las.app.cli:main`
  (during migration: `las.cli:main` re-export shim). The CLI is a thin driver over the
  engine library; the M1 HTTP API is a second, equally thin driver (`studio serve`).
- Startup: load settings from `studio.db`, discover module manifests
  (`las/modules/**/manifest.py`, any nesting depth), resolve enabled modules,
  register contributed CLI commands, refuse disabled core modules with an actionable
  error. Disablement takes effect at process start (no hot-unload in Python).
- Build/package: `pip wheel .` produces a dependency-free wheel; tests run with
  `python -m unittest discover -s tests`; CI runs the suite plus architecture checks
  on Python 3.10/3.11/3.12.

## 4. Module hierarchy

See `MODULE_MAP.md` (current paths and migration targets in one table; kept in sync
by the registry test once manifests land in S2).

## 5. Dependency rules

See `DEPENDENCY_RULES.md`.

## 6. Contracts and communication

- Synchronous in-process calls through module facades only (no event bus).
- The durable event log (spec 6.3, hash-chained, append-only) is the audit and
  integration record; every state change is an event written before the next action.
- Public API stability: within v0, facade exports may change with a changelog entry;
  from 1.0, facade changes follow semantic versioning of the package.

## 7. Data ownership and lifecycle

| Data | Owning module | Notes |
|---|---|---|
| `studio.db` (events, providers, plans, templates, role_profiles, settings, meta) | storage | Events are the source of truth; derived tables rebuild from the log |
| `runs/<id>/attempts/...`, `runs/<id>/handoffs/...` | execution | Written through the storage port; logical ownership documented here |
| `runs/<id>/workspace/...` | security | Per-run jailed file area |
| `runs/<id>/export/...` | reporting | Markdown/JSON exports with provenance |
| `plans/`, `templates/`, `logs/`, `backups/` | storage | JSON copies beside the DB; backups before migrations |

Disabling or removing a module never deletes user data: exports and run data survive
reporting removal; workspace files survive security changes; deleting a run or all data
is an explicit, separately confirmed operation (SET-2).

## 8. Module lifecycle

- **Enable/disable**: settings key `module.<id>.enabled` (default: manifest's
  `default_enabled`). Enforcement at authoritative boundaries: the registry refuses to
  hand out disabled modules, contributed CLI commands are not registered, and engine
  paths degrade per declared required/optional dependencies. Core modules (storage,
  security, execution, context, planning, planquality, providers, handoffs, shared)
  cannot be disabled — the registry returns an actionable error naming the dependent.
- **Build-time inclusion**: the single wheel ships all modules; excluding a module from
  a build means removing its directory (manifest scan then does not find it).
- **Source removal**: delete the module directory plus its manifest-declared test
  files; re-scan; suite must stay green. Verified by `tools/removal_test.py` on a
  disposable copy — never the working tree.
- **Runtime unload**: not supported (Python cannot safely unload code); disablement is
  effective at the next process start. Documented, not hidden.
- **Data disposition**: per the table above; always retain on disable/removal.

## 9. Verification

| Concern | Command/check | Where it runs | Current status |
|---|---|---|---|
| Build | `pip wheel .` | local + CI | planned (S6) |
| Tests | `python -m unittest discover -s tests` | local + CI | passing (181 tests, M0) |
| Dependency boundaries | `tests/test_architecture.py` (AST checker) | suite + CI | planned (S3) |
| Contract validation | facade `__all__` + manifest declaration rules | suite + CI | planned (S2/S3) |
| Registration/discovery | `tests/test_registry.py` | suite + CI | planned (S2) |
| Removal test | `tools/removal_test.py` | on demand + periodic CI | planned (S6) |
| Disabled-state tests | `tests/test_module_lifecycle.py` | suite + CI | planned (S6) |
| Packaging/runtime smoke | install wheel into venv, run `studio selftest` | CI | planned (S6) |

## 10. Material architecture decisions

See `docs/adr/` — 0001 shared kernel, 0002 registry and manifests, 0003 stdlib
architecture checker, 0004 incremental migration. New material decisions get an ADR.
