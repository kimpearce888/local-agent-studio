# ADR-0004: Incremental migration S0-S7 with behavior-preserving gates

- Status: accepted
- Date: 2026-11-21
- Decision owners: engineering (architecture)
- Affected modules: all

## Context

The approved architecture assessment proposes a modular target (host + shared kernel
+ feature modules) for an existing, tested M0 codebase. The standard forbids
re-architecturing a sound codebase merely to match a preferred layout, and requires
preserving existing functionality and user data. The M0 layout is disciplined (no
cycles, no private-name imports, 181 green tests) but has no registry, contracts,
enablement or enforcement (findings F-01 … F-11).

## Decision

Migrate in eight gated steps, each a single releasable commit, each keeping the full
suite green and changing no on-disk data format:

- S0 documentation baseline (this ADR set; describes current reality + target).
- S1 extract `las/shared` kernel; old paths become re-export shims; `ProviderProfile`
  moves out of storage (fixes F-01's inversion).
- S2 pilot: registry + manifests; carve `reporting` out end to end (manifest, facade,
  registry entry, enable/disable, disabled-state test, disposable removal test);
  register `providers.mock` as a child module; `SimulatedCrash` moves to
  `shared.provider_contract` (fixes F-02).
- S3 architecture checker + registry integrity tests, wired into the suite.
- S4 mechanical migration of remaining modules into `las/modules/` via `git mv` plus
  scripted import rewrites; shims keep old paths; **test files are not edited** —
  they keep importing old paths, proving the shims preserve the API.
- S5 split god-modules: `scheduler.py` (851 lines) into execution children
  (driver/gates/failures/budgets/controls), `cli.py` (689 lines) into a thin host
  adapter plus module-contributed commands; driver receives Workspace/PermissionBroker
  factories (fixes F-07, F-10).
- S6 lifecycle tests (disabled-state, failure isolation, replacement), removal-test
  harness, CI workflow on the Python matrix, push and verify.
- S7 remove shims, rewrite test imports to canonical paths, finalize docs.

Rollback: any step reverts as one commit; S4's shims make the wide move bisectable.

## Alternatives considered

- Big-bang restructure in one commit: rejected — unverifiable, unreviewable, high
  risk to a green codebase.
- Do not migrate, add only docs: rejected — the standard's module qualities
  (contracts, enablement, removal) cannot be demonstrated without the registry and
  boundaries; M1-M4 growth multiplies the cost later.

## Consequences

Benefits: continuous releasability; each step has a machine-checked gate; user data
and event-log formats untouched throughout (existing runs keep verifying). Costs: a
transition period where shims exist; two steps (S4, S5) are wide and need the
untouched suite as their safety net.

## Verification

Gate per step: `python3 -m unittest discover -s tests` green plus the step's own
checks (pilot acceptance per bootstrap Phase 8; architecture checks from S3; CI from
S6). S4's gate additionally requires zero diffs under `tests/`.
