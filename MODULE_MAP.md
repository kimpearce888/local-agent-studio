# Module Map

> One row per independently meaningful module. This map describes implemented reality;
> rows are updated when code moves (migration S4/S7). "Target path" is the module's
> location after the modular migration approved in the architecture assessment
> (see `docs/adr/0004`).

| Module ID | Parent | Responsibility | Public contract | Owned data | Required dependencies | Optional dependencies | Owner/path (current → target) | Test command |
|---|---|---|---|---|---|---|---|---|
| shared | host | Cross-cutting contracts: plan schema types, provider port, event types, message catalogue | `las.shared` (facade modules) | none | none | none | `las/schema`, `las/messages.py` → `las/shared/` | `python -m unittest tests.test_plan_validation tests.test_handoff_validation` |
| providers | host | Provider connectivity: wire adapters, presets, capability probe | `las.providers` (`Adapter`, `build_adapter`, `ProviderRegistry`, `probe`, `PRESETS`) | none | shared | none | `las/providers/` → `las/modules/providers/` | `python -m unittest tests.test_adapters` |
| providers.mock | providers | Deterministic scripted mock provider (tests, demo, self-test) | `las.providers.mock` (`MockAdapter`, `SimulatedCrash`, scripted turns) | none | shared | none | `las/providers/mock.py` → `las/modules/providers/mock/` | `python -m unittest tests.test_adapters` |
| planning | host | Goal-to-plan: planner contract, repair loop, critique pass, constraint extraction | `las.planner` (`Planner`, `PLAN_JSON_SCHEMA`) | none | shared, planquality | none | `las/planner.py` → `las/modules/planning/` | `python -m unittest tests.test_planner` |
| planquality | host | Plan validation (V rules) and linting (L rules) | `las.schema.plan` + `las.linter` (`validate_plan`, `lint`) | none | shared | providers (reachability input only) | `las/schema/plan.py`, `las/linter.py` → `las/modules/planquality/` | `python -m unittest tests.test_plan_validation tests.test_linter` |
| execution | host | Sequential run driver: gates, failure table, caps, controls, replay, rerun | `las.scheduler` + `las.runstate` (`Engine`, `RunDriver`, `StepResult`, `DriverConfig`) | `runs/<id>/attempts/*`, `runs/<id>/handoffs/*` | shared, storage, context, security, handoffs contracts | none | `las/scheduler.py`, `las/runstate.py` → `las/modules/execution/` | `python -m unittest tests.test_scheduler` |
| context | host | Context assembly (blocks 1-7), token budget, compaction ladder | `las.context` (`assemble_context`, protocol text) | none | shared, security, handoffs contracts | none | `las/context.py` → `las/modules/context/` | `python -m unittest tests.test_context` |
| handoffs | host | Handoff schema, `<handoff>` extraction, H-rule validation | `las.schema.handoff` (`Handoff`, `validate_handoff`) | none | shared | none | `las/schema/handoff.py` → `las/modules/handoffs/` | `python -m unittest tests.test_handoff_validation` |
| security | host | Agency control: workspace jail, tool registry, permission broker, Rule of Two | `las.workspace` + `las.tools` (`Workspace`, `PermissionBroker`, `compute_flags`) | `runs/<id>/workspace/*` | shared | none | `las/workspace.py`, `las/tools.py` → `las/modules/security/` | `python -m unittest tests.test_workspace tests.test_tools_broker` |
| storage | host | SQLite WAL store: hash-chained event log, providers/plans/templates/settings, run dirs | `las.store` (`Store`) | `studio.db`, `runs/` tree, `backups/` | shared | none | `las/store.py` → `las/modules/storage/` | `python -m unittest tests.test_store_events` |
| reporting | host | Markdown/JSON export with provenance and AI notice | `las.export` (`export_run`, `ExportOptions`) | `runs/<id>/export/*` | shared, storage | none | `las/export.py` → `las/modules/reporting/` | `python -m unittest tests.test_cli` (export paths) |
| selftest | host | Built-in three-task self-test with hard caps | `las.selftest` (`run_selftest`) | none | shared, execution | providers.mock | `las/selftest.py` → `las/modules/selftest/` | `python -m unittest tests.test_cli` (selftest paths) |
| app | root | Host: composition root, module registry, capability resolution, CLI adapter | `las.cli` → `las.app` | none | all module contracts | none | `las/cli.py` → `las/app/` | `python -m unittest tests.test_cli` |

## Rules

- Modules are identified by capability and ownership, not file count.
- The same independence test applies recursively to meaningful subfeatures
  (`providers.mock` is the first registered child module).
- Parent modules compose children; child implementation stays private to the child.
- Trivial helpers are not modules; they stay inside the module that owns them.
- Every module ships a `manifest.py` (from S2) declaring identity, dependencies and
  contributed commands; the registry test keeps this map and the code in sync.
