# Project Agent Instructions

This file records project-specific facts and commands. It supplements, and does not
silently replace, the user's Global AI Agent Development Standard (GAADS 1.0.0):
Agent-First Recursive Modular Architecture, recursive feature independence at every
meaningful capability boundary, executable architecture checks, truthful verification
reporting, and safe module lifecycle behavior.

## Required global standard

For software engineering tasks, load `agent-first-software-development` and the
relevant supporting skills (`recursive-feature-independence` always for module work;
`project-architecture-bootstrap`, `architecture-enforcement`, `agent-task-execution`
when triggered). If global Skills are unavailable, read the installed `SKILL.md`
files directly; if those are unavailable, follow this file and report the limitation.
Never claim a skill was loaded unless it was actually read.

## Project identity

- Product/system: Local Agent Studio — local-first chained-task agent runner.
- Project type: modular monolith (library engine + CLI + loopback HTTP API).
- Primary language: Python 3.10+, standard library only (project decision, OQ-2).
- Frameworks/runtime: none beyond stdlib (sqlite3, urllib, argparse, http.server).
- Build/package tooling: setuptools via `pyproject.toml`; console script `studio`.
- Supported platforms: Windows 11, macOS (current + 2), Ubuntu 22.04+.
- Deployment/distribution model: pip wheel now; signed installers at M4; MIT licence.

## Architecture entry points

- Overview: `ARCHITECTURE.md`
- Module ownership map: `MODULE_MAP.md`
- Dependency rules: `DEPENDENCY_RULES.md`
- Architecture decisions: `docs/adr/`
- Roadmap and phase decisions: `docs/PLAN.md`
- Requirements: `docs/prd.md`, `docs/product-spec.md` (copies of the source documents)

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e .` (no third-party deps)
- Build: `pip wheel .` (output into `dist/`)
- Format/lint: none configured (stdlib only; keep code tidy manually)
- Type/static checks: none configured (stdlib only)
- Focused tests: `python3 -m unittest tests.test_scheduler` (one module at a time)
- Full tests: `python3 -m unittest discover -s tests`
- Run application: `python3 -m las --help`, `python3 -m las selftest`,
  `python3 -m las run PLAN.json --provider mock`
- Package/release: `pip wheel . && twine check dist/*` (twine not vendored; CI covers)
- Architecture checks: included in the suite (`tests.test_architecture`,
  `tests.test_registry`); module removal: `python3 tools/removal_test.py MODULE_ID`

Do not invent commands. Verify them from the repository and update this section when
they change.

## Project-specific constraints

- Data retention/deletion: never delete user data on module disable/removal; run and
  data deletion are explicit, confirmed operations (SET-2); DB backups before
  migrations; refuse databases written by a newer version (DB-NEWER).
- Security/privacy: no network except user-configured provider endpoints; provider
  keys only as `env:NAME` references in v0 (never in plans/events/exports); workspace
  jail with symlink resolution; provider-side tools never enabled (PRV-5); loopback
  + per-launch token + origin check for the HTTP API (SEC, spec 7.5).
- Performance: canvas/UI targets are M2; engine targets: caps enforced within one
  model call (RUN-8), resume correctness over speed.
- Compatibility: plan schema v1 and event schema are versioned; forward-compatible
  migrations with backup before migrating.
- Offline/network constraints: everything must work with no internet after install.
- Licensing/dependency constraints: standard library only; MIT for this repo.

## Working rules

- Inspect `MODULE_MAP.md`, `DEPENDENCY_RULES.md` and the working tree before editing.
- Preserve unrelated user changes; keep commits small and releasable.
- Work inside the owning module; use public contracts only; declare dependencies in
  the module manifest; never import another module's private files.
- Run the narrowest relevant tests first, then the full suite and architecture checks.
- Report actual commands and outcomes; distinguish passed / failed / blocked /
  skipped / not run. Never claim untested behavior works.
- New modules need: manifest, facade contract with `__all__`, tests named in the
  manifest, a row in `MODULE_MAP.md`, and green architecture + registry checks.
- Document material deviations and why they are necessary (ADR + DEPENDENCY_RULES
  exception row if boundary-related).
