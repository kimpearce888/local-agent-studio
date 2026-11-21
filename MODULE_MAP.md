# Module Map

> One row per independently meaningful module. This map describes implemented reality;
> rows describe implemented reality (all modules at their canonical paths
> since migration step S7; see `docs/adr/0004`).

| Module ID | Parent | Responsibility | Public contract | Owned data | Required dependencies | Optional dependencies | Owner/path (current → target) | Test command |
|---|---|---|---|---|---|---|---|---|
| shared | host | Cross-cutting contracts: plan schema types, provider port, event types, message catalogue | `las.shared` (facade modules) | none | none | none | `las/shared/` (implemented) | `python -m unittest tests.test_plan_validation tests.test_handoff_validation` |
| providers | host | Provider connectivity: wire adapters, presets, capability probe | `las.modules.providers` (`Adapter`, `build_adapter`, `ProviderRegistry`, `probe`, `PRESETS`) | none | shared | none | `las/modules/providers/` (implemented) | `python -m unittest tests.test_adapters` |
| providers.mock | providers | Deterministic scripted mock provider (tests, demo, self-test) | `las.modules.providers.mock` (`MockAdapter`, `SimulatedCrash`, scripted turns) | none | shared | none | `las/modules/providers/mock/` (implemented) | `python -m unittest tests.test_adapters` |
| planning | host | Goal-to-plan: planner contract, repair loop, critique pass, constraint extraction | `las.planner` (`Planner`, `PLAN_JSON_SCHEMA`) | none | shared, planquality | none | `las/modules/planning/` (implemented) | `python -m unittest tests.test_planner` |
| planquality | host | Plan validation (V rules) and linting (L rules) | `las.modules.planquality` (`validate_plan`, `lint`) | none | shared | providers (reachability input only) | `las/schema/plan.py`, `las/linter.py` → `las/modules/planquality/` | `python -m unittest tests.test_plan_validation tests.test_linter` |
| execution | host | Sequential run driver: gates, failure table, caps, controls, replay, rerun | `las.modules.execution` (`Engine`, `RunDriver`, `StepResult`, `DriverConfig`) | `runs/<id>/attempts/*`, `runs/<id>/handoffs/*` | shared, storage, context, security, handoffs contracts | none | `las/scheduler.py`, `las/runstate.py` → `las/modules/execution/` | `python -m unittest tests.test_scheduler` |
| context | host | Context assembly (blocks 1-7), token budget, compaction ladder | `las.modules.context` (`assemble_context`, protocol text) | none | shared, security, handoffs contracts | none | `las/modules/context/` (implemented) | `python -m unittest tests.test_context` |
| handoffs | host | Handoff schema, `<handoff>` extraction, H-rule validation | `las.modules.handoffs` (`Handoff`, `validate_handoff`) | none | shared | none | `las/modules/handoffs/` (implemented) | `python -m unittest tests.test_handoff_validation` |
| security | host | Agency control: workspace jail, tool registry, permission broker, Rule of Two | `las.modules.security` (`Workspace`, `PermissionBroker`, `compute_flags`) | `runs/<id>/workspace/*` | shared | none | `las/workspace.py`, `las/tools.py` → `las/modules/security/` | `python -m unittest tests.test_workspace tests.test_tools_broker` |
| storage | host | SQLite WAL store: hash-chained event log, providers/plans/templates/settings, run dirs | `las.modules.storage` (`Store`) | `studio.db`, `runs/` tree, `backups/` | shared | none | `las/modules/storage/` (implemented) | `python -m unittest tests.test_store_events` |
| reporting | host | Markdown/JSON export with provenance and AI notice | `las.modules.reporting` (`export_run`, `ExportOptions`) | `runs/<id>/export/*` | shared, storage | none | `las/modules/reporting/` (implemented) | `python -m unittest tests.test_cli` (export paths) |
| selftest | host | Built-in three-task self-test with hard caps | `las.modules.selftest` (`run_selftest`) | none | shared, execution | providers.mock | `las/modules/selftest/` (implemented) | `python -m unittest tests.test_cli` (selftest paths) |
| api | host | Loopback engine HTTP API: token auth, origin check, runs/events/controls (spec 7.5) | `las.modules.api` (`serve`, `serve()`) | none | shared, execution, planning, providers, storage | reporting (export endpoint) | `las/modules/api/` (implemented) | `python -m unittest tests.test_api` |
| library | host | Templates (LIB-1) and role profiles (LIB-2), spec S10 | `las.modules.library` (`save_template`, `builtin_templates`, `save_role_profile`) | templates + role_profiles tables | planquality, storage | none | `las/modules/library/` (implemented) | `python -m unittest tests.test_library` |
| diagnostics | host | Redacted local diagnostics bundle (OBS-7, S13) | `las.modules.diagnostics` (`build_diagnostics`, `text_meta`) | none | execution, storage | none | `las/modules/diagnostics/` (implemented) | `python -m unittest tests.test_diagnostics` |
| app | root | Host: composition root, module registry, capability resolution, CLI adapter | `las/app/` (implemented) | none | all module contracts | none | `las/app/` (implemented) | `python -m unittest tests.test_cli` |

## Rules

- Modules are identified by capability and ownership, not file count.
- The same independence test applies recursively to meaningful subfeatures
  (`providers.mock` is the first registered child module).
- Parent modules compose children; child implementation stays private to the child.
- Trivial helpers are not modules; they stay inside the module that owns them.
- Every module ships a `manifest.py` (from S2) declaring identity, dependencies and
  contributed commands; the registry test keeps this map and the code in sync.
