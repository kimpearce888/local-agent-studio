# User guide

The engine behind Local Agent Studio. This guide documents the CLI, the
loopback HTTP API and the everyday workflows. Every command accepts
`--data-dir DIR` (default `~/.local-agent-studio/`, or `$LAS_DATA_DIR`)
and prints JSON with `--json` where a list is involved.

Exit codes: `0` success - `2` usage/invalid input - `3` provider problem -
`4` plan invalid - `5` run failed - `6` gate refused - `7` internal error.

## Life cycle of a run

1. `plan` turns a goal into a validated plan (PLN-1 to PLN-5). Constraints
   are extracted and listed for your review before the plan is saved.
2. `run` executes the plan strictly sequentially, one session per task,
   in plan order (RUN-1, RUN-2). Each task sees only its role prompt, the
   goal, its instructions, its binding constraints, the handoffs in
   `needs` and the workspace manifest.
3. Controls and gates: `approve`, `reject`, `retry`, `skip`, `rerun`,
   `resume`, `edit` (RUN-6). Hard caps on tokens, wall time, turns and
   tool calls protect every run (RUN-8).
4. `export` writes a Markdown report or a re-importable JSON bundle with
   provenance (OBS-4); `verify` re-checks the hash chain (RUN-5).

## Providers and models

```bash
python3 -m las providers detect                  # scan local ports
python3 -m las providers add --name P --preset lmstudio [--auth-env env:KEY]
python3 -m las providers list [--json]
python3 -m las providers test --provider P       # capability probe (PRV-4)
python3 -m las models list --provider P          # loaded flag per model
python3 -m las models load --provider P --model M    # PRV-8, llama.cpp router
python3 -m las models unload --provider P --model M
```

Presets: `lmstudio` (1234), `unsloth` (port shown by the server), `ollama`
(11434), `llamacpp` (8080, router-mode lifecycle), `vllm` (8000), `generic`.
Unsloth always sends `enable_tools: false` (PRV-5). Model lifecycle uses
the llama.cpp router endpoints; LM Studio's native endpoints are confirmed
against a live server in Phase 0, so the engine refuses them honestly
until then.

## Planning and running

```bash
python3 -m las plan "GOAL" --provider P [--model M]
                [--granularity coarse|normal|fine] [--max-tasks N]
                [--attach FILE ...]     # attachments (PLN-7): manifest + summaries
python3 -m las run PLAN_OR_TEMPLATE [--var name=value ...]
                [--gate-before TASK] [--failure-policy stop|skip|ask]
python3 -m las list [--json]           # OBS-1 history
python3 -m las inspect RUN [--task T] [--json]   # OBS-2 states + handoffs
```

`run` replaces every `{{variable}}` (V-12), validates the plan, and refuses
to start anything invalid. Per-task `model` and `params` overrides are
honoured and shown in `inspect` (PRV-6).

## Controls (RUN-6)

```bash
python3 -m las approve RUN TASK        # gate: run it
python3 -m las reject RUN TASK         # gate: stop the run
python3 -m las retry RUN TASK          # after failure (policy ask)
python3 -m las skip RUN TASK           # dependents are skipped too
python3 -m las rerun RUN TASK          # downstream results go stale
python3 -m las resume RUN              # paused or failed runs
python3 -m las edit RUN TASK --instructions "..." [--title T]
                [--model-provider P --model-id M] [--reason WHY]
```

`edit` changes only pending tasks, is refused on terminal runs, and is
recorded as a `task.edited` event - the run executes the edited plan even
after a crash, because replay applies edits (RUN-5).

## Re-planning (PLN-6)

```bash
python3 -m las replan RUN                     # propose: shows the diff, stores it
python3 -m las replan RUN --apply PROPOSAL_ID # apply exactly the shown diff
```

A replan replaces the remaining (pending) tasks with the proposal. Tasks
you edited stay pinned and are kept; their outputs must be consumed by
the new tasks. The proposal is a stored plan, so you approve exactly the
diff that was shown.

## Comparing runs (OBS-6)

```bash
python3 -m las compare RUN_A RUN_B [--json]
```

Plan diff (tasks added/removed/changed, constraints reworded) and output
diff (per common task: status, handoff summary, tokens).

## Library (LIB-1, LIB-2)

```bash
python3 -m las templates list [--json]
python3 -m las templates save PLAN.json --name "Mine" [--description D]
python3 -m las templates duplicate ID [--new-name N]
python3 -m las templates export ID [-o FILE]
python3 -m las templates delete ID       # built-ins are refused, duplicate them
python3 -m las roles list [--json]
python3 -m las roles save ROLE.json      # {id, name, role, system_prompt,
                                         #  params, tools, default_model}
python3 -m las roles delete ID
```

Built-in templates: research report, document analysis, code review,
content pipeline - all with variables.

## Data, deletion and retention (SET-2)

```bash
python3 -m las data status              # data dir, run count, retention
python3 -m las data prune [--days N]    # apply retention (keep forever/90/30)
python3 -m las data delete-all --confirm
python3 -m las delete RUN --confirm     # one run + its workspace
```

Deletion removes runs, workspaces and exports; providers, plans,
templates, role profiles and settings survive.

## Diagnostics (OBS-7)

```bash
python3 -m las diagnostics [-o FILE]
```

Prints everything the bundle will contain, then writes it: goal and titles
as length + SHA-256 only, keys never included. Nothing is sent anywhere.

## Modules

```bash
python3 -m las modules list             # registry: id, version, enabled
python3 -m las modules disable ID       # non-core modules only
python3 -m las modules enable ID
```

Disabling a module removes its commands and features with an actionable
message; core modules (storage, execution, ...) are protected, and a
module others require cannot be disabled. Every removal is verified by
`tools/removal_test.py` in the repository.

## The loopback HTTP API (API-2, spec 7.5)

```bash
python3 -m las serve [--port N]         # 127.0.0.1 only; token in <data>/api-token
```

Every request carries `X-Studio-Token`. Cross-origin browser requests are
rejected. Endpoints: `GET /runs`, `POST /runs`, `GET /runs/{id}`,
`DELETE /runs/{id}`, `GET /runs/{id}/events?after=SEQ`, `POST /runs/{id}`
controls (`pause`, `resume`, `cancel`, `approve`, `reject`, `retry`,
`skip`, `rerun`), `PATCH /runs/{id}/tasks/{tid}` (edit a pending task),
`GET /runs/{id}/export`, `GET|POST /providers`, `PUT|DELETE
/providers/{id}`, `POST /providers/{id}/probe`, `POST
/providers/{id}/models/{model}/load|unload`, `GET|POST /plans`,
`GET /templates`. An OpenAPI description is served at `/openapi.json`.

## Self-test and verification

```bash
python3 -m las selftest                 # three-task run against the mock
python3 -m las verify RUN               # event hash chain
```

Run the full test suite from the repository:
`python3 -m unittest discover -s tests` (281 tests).
