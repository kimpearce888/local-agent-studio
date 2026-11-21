# Plan schema reference (schema v1)

A plan is one JSON object. This page documents every field, every
validation rule and every lint rule exactly as the engine enforces them
(`las/modules/planquality`). The authoritative short form lives in the
product specification, section 6.1.

## Top-level fields

| Field | Type | Rule | Notes |
| --- | --- | --- | --- |
| `schema_version` | integer | V-01 | Always `1`. |
| `id` | string | V-01 | Plan id, e.g. `p-20261121`. |
| `title` | string | - | Human title. |
| `goal` | string | - | The goal the planner worked from. |
| `created_at` | string | - | ISO timestamp. |
| `options` | object | - | `granularity` (`coarse`/`normal`/`fine`), `max_tasks`, `stage_barriers` (PLN-5). |
| `constraints` | array | V-09 | Binding constraints: `{id, text}`. Ids unique, text non-empty. |
| `tasks` | array | V-02+ | At least one task; the last task must be a collector (V-08). |

## Task fields

| Field | Type | Required | Rule | Notes |
| --- | --- | --- | --- | --- |
| `id` | string | Yes | V-02 | Lowercase letters, digits, `-` or `_`; unique. |
| `type` | enum | Yes | - | `task`, `collector`, `gate`, `input`, `output`, `note`. |
| `title` | string | Yes | V-05 | 1-80 characters. |
| `instructions` | string | Yes (except gate/note) | V-05 | 20-4,000 characters. |
| `needs` | string[] | Yes | V-03, V-07 | Earlier task ids only, at most 8. |
| `expected_output` | string | No | - | Free text, e.g. `result.md`. |
| `constraints` | string[] | No | V-07, V-10 | Ids from the plan's constraints, at most 12. |
| `role` | enum | No | - | `planner`, `worker` or `collector`; default `worker`. |
| `model` | object | No | - | Per-task override `{provider_id, model_id}` (PRV-6). |
| `params` | object | No | - | `temperature`, `top_p`, `max_tokens`, `seed`, `stop`, `context_length`. |
| `tools` | string[] | No | V-11 | From the tool catalogue; default the five workspace file tools. |
| `limits` | object | No | - | `retries`, `timeout_seconds`, `max_turns`, `max_tool_calls`, `max_tokens`. |
| `gate_before` | boolean | No | L-09 | When true, an approval gate runs before the task. |

## Validation rules (V-01 to V-12)

- **V-01** schema version and required top-level fields (enforced on parse).
- **V-02** task ids are valid and unique.
- **V-03** every `needs` entry exists and comes earlier (no cycles, no forward references).
- **V-05** titles exist (max 80 chars); instructions exist (min 20 chars, max 4,000).
- **V-06** the plan respects `options.max_tasks`.
- **V-07** at most 8 inputs and 12 constraints per task.
- **V-08** the final task is a collector.
- **V-09** constraint ids unique, text non-empty.
- **V-10** every task constraint id exists in the plan.
- **V-11** tool names exist in the catalogue.
- **V-12** every `{{variable}}` in a task has a value at run time (templates: pass `--var name=value`).

An invalid plan can never reach the scheduler: `plan` repairs up to three
rounds, then hands you the marked-up plan (PLN-2).

## Lint rules (L-01 to L-09)

Errors block runs, warnings and infos do not: missing dependencies (L-01),
oversized tasks (L-02), unused upstream results (L-03), predicted context
overflow (L-04), constraints no task carries (L-05), and their neighbours.
Three of three Rule-of-Two properties without a gate is an error (L-09).

## A complete example

```json
{
  "schema_version": 1,
  "id": "p-demo",
  "title": "Research report",
  "goal": "Produce a short, sourced research report about {{topic}}.",
  "created_at": "2026-11-21T12:00:00Z",
  "options": {"granularity": "normal", "max_tasks": 30, "stage_barriers": false},
  "constraints": [
    {"id": "c1", "text": "You must cite every figure."},
    {"id": "c2", "text": "Never invent numbers."}
  ],
  "tasks": [
    {"id": "gather", "title": "Gather facts",
     "instructions": "Gather five sourced facts about {{topic}} and write them to facts.md with a source for each.",
     "needs": [], "expected_output": "facts.md", "constraints": ["c1", "c2"],
     "role": "worker"},
    {"id": "collect", "title": "Collect the final report",
     "instructions": "Combine the earlier outputs into the final report at result.md and check every citation.",
     "needs": ["gather"], "expected_output": "result.md",
     "constraints": ["c1", "c2"], "role": "collector"}
  ]
}
```

Run it: `python3 -m las run p-demo --var topic=local-first software`.
