# ADR-0001: Extract a small shared kernel (`las.shared`) for cross-module contracts

- Status: accepted
- Date: 2026-11-21
- Decision owners: engineering (architecture)
- Affected modules: all

## Context

The M0 layout keeps genuinely shared domain types inside feature packages: the plan
schema lives in `las/schema`, the provider port in `las/providers/base.py`, event
payload shapes in `las/runstate.py`, the message catalogue in `las/messages.py`. Six
or more modules import these. The provider layer also imports storage
(`ProviderProfile` in `las/store.py`, finding F-01), which inverts the intended
dependency direction, and six files bypass the providers facade to import
`las.providers.base` directly (F-08). Without a kernel, "shared" types have no home
that is allowed to be imported by everyone.

## Decision

Create `las/shared/` holding only contracts used by three or more modules:
`plan_contract` (Plan/Task/Constraint types), `provider_contract` (Adapter port,
message model, ToolDef, normalized errors, SimulatedCrash fault-injection signal),
`events_contract` (event types and payload shapes), `messages` (message catalogue),
and `module_contract` (manifest types for the registry). `ProviderProfile` moves from
storage into `provider_contract`. The kernel imports nothing inside `las`. Every
kernel change is reviewed like a public API. Old import paths keep working through
re-export shims during migration, then are removed (S7).

## Alternatives considered

- Leave shared types in their current packages and allow cross-imports: rejected —
  no acyclic rule can hold, and F-01/F-08 remain unfixable.
- A full `common/` dumping ground: rejected by the standard (RFI §5) and by review
  discipline; the three-consumer threshold prevents it.

## Consequences

Benefits: F-01 and F-08 become fixable; providers can stay inverted; every module may
depend on the kernel without declaration overhead. Costs: one more package; kernel
changes touch many modules, so they are deliberate and rare. Feature logic never
enters the kernel (validation rules stay in `planquality`, replay logic in
`execution`).

## Verification

`tests/test_architecture.py` kernel-purity rule (shared imports nothing internal);
existing suites keep passing unchanged through shims; facade-only imports enforced
after S3.
