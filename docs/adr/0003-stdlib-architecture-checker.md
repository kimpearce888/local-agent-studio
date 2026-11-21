# ADR-0003: Stdlib AST architecture checker instead of import-linter

- Status: accepted
- Date: 2026-11-21
- Decision owners: engineering (architecture)
- Affected modules: all; tests; CI

## Context

The project's Phase-0 decision is Python standard library only (OQ-2), which keeps
the install-and-run promise and the supply-chain surface minimal. Architecture rules
that matter must be executable (GAAPS §6); an instruction never executed is not
enforcement. `import-linter` is the obvious off-the-shelf tool but is a third-party
dependency and adds a config layer plus a plugin contract of its own.

## Decision

Implement the boundary checker with the standard library `ast` module as
`tests/test_architecture.py`, part of the normal unittest suite (so it runs on every
`python -m unittest discover` and in CI, with no separate invocation to forget).
Rules: (1) kernel purity — `las/shared` imports nothing internal; (2) host is never a
dependency — `las/modules/**` must not import `las.app`; (3) facade-only access — a
module may import another module only via its package facade or a registered child
facade, never a private file; (4) private names — no cross-module import of
underscore names; (5) declaration — every cross-module import must be declared in the
importer's manifest (`requires`/`optional`); (6) no cycles in actual imports;
(7) forbidden edges from `DEPENDENCY_RULES.md` (providers→storage/execution/context,
storage→modules, production→providers.mock except declared). Failure messages name
the source file, target, rule and remedy.

## Alternatives considered

- `import-linter` as a dev-only dependency: deferred — the stdlib checker covers the
  declared rules; adding it later for layered contracts is compatible. Recorded as
  an option, not a commitment.
- Pre-commit hooks alone: rejected as the only mechanism — nothing runs them in CI
  for this project yet, and the suite is the always-run vehicle.

## Consequences

Benefits: zero new dependencies; checks run exactly where tests run; rules live in
reviewable Python next to the manifests they check. Costs: we maintain ~200 lines of
checker code; AST-based import analysis must handle `from X import Y`, `import X`,
and attribute access forms — covered, and the checker fails closed on parse errors.

## Verification

The checker itself is tested: mutation tests flip one rule at a time (a deliberately
violating fixture file inside `tests/fixtures/`) and each check must fail. CI runs
the full suite on 3.10/3.11/3.12.
