# Handover — Local Agent Studio architecture migration and M1

## Where things stand (2026-11-21)

- Repository: github.com/kimpearce888/local-agent-studio, branch main,
  everything committed and pushed. Full suite green (unittest discover).
- The GAADS architecture migration S0-S7 is COMPLETE: no legacy shims,
  canonical imports everywhere, 14 registered modules (incl. the
  providers.mock child), all enforcement checks green.
- The M1 loopback HTTP API is implemented and tested (`studio serve`),
  including model lifecycle endpoints (PRV-8).
- P0 features delivered: templates + role profiles (`studio templates`,
  `studio roles`), diagnostics bundle (`studio diagnostics`), data
  retention/delete (`studio data`, `studio delete`), model lifecycle
  (`studio models`).
- Requirements sweep complete: every PRD ID is mapped with status and
  evidence in docs/requirements-coverage.md. This session added edit
  pending tasks (RUN-6), the four PRD-named built-in templates (LIB-1),
  single-run deletion (SET-2), the user documentation set (DST-3),
  OpenAPI + completion hooks (API-2), run comparison (OBS-6), OTLP
  telemetry (OBS-5), re-planning with pinned edits (PLN-6), attachments
  (PLN-7/TLS-8), prompt version history (LIB-3) and the release policy
  (DST-4).

## Run everything

```bash
python3 -m unittest discover -s tests        # full suite incl. architecture checks
python tools/removal_test.py reporting       # disposable module removal test
python3 -m las selftest                      # end-to-end against the mock
python3 -m las serve --port 0                # loopback API (token in <data>/api-token)
python3 -m las templates list                # built-in + user templates
python3 -m las replan RUN --apply PROPOSAL    # re-planning (PLN-6)
python3 -m las compare RUN_A RUN_B            # run diff (OBS-6)
python3 -m las data status                   # retention and run counts
```

## Architecture quick map

- las/app — host: registry (manifest discovery), capabilities (enable/disable), CLI
- las/shared — kernel: plan/provider/events/message/module contracts, tool catalog
- las/modules — providers(+mock child), planning, planquality, execution
  (driver/attempt/gates/controls/budgets/engine/runstate), context, handoffs,
  security (workspace/tools), storage, reporting, selftest, api
- Enforcement: tests/test_architecture.py (9 AST rules + size fitness),
  tests/test_registry.py, tests/test_module_lifecycle.py,
  tools/removal_test.py — all run in the normal suite and in CI.

## Caveats recorded honestly

- CI runs on GitHub; outcomes were not observable from this sandbox
  (identical local commands passed).
- The host cli.py remains a documented size exception (S5b deferred).
- No legacy shims remain (S7 done); R8 enforces the structural invariant.
- One broken intermediate commit (17be6dd) was pushed during M1 work and
  repaired in the following commit; history shows both.
