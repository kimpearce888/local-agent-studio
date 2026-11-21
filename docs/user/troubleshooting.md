# Troubleshooting

Every engine failure prints a reason code, a one-line explanation and the
exact fix. This page lists the codes in the engine's message catalogue and
what to do about each. Exit codes: `0` success, `2` usage error, `3`
provider problem, `4` plan invalid, `5` run failed, `6` gate refused,
`7` internal error.

## Provider and connection

| Code | Meaning | Fix |
| --- | --- | --- |
| `UNREACHABLE` | The server at the URL did not answer. | Start the server and its local API, then run `providers test` again. Check the port: LM Studio defaults to `http://localhost:1234`, llama.cpp to `:8080`, Ollama to `:11434`, vLLM to `:8000`. `providers detect` scans them for you. |
| `AUTH` | The server rejected the key. | Create a new key in the server's settings and replace the value in the environment variable your profile references with `--auth-env`. |
| `MODEL_NOT_FOUND` | The model is not on this server. | Load the model in the server, or pick another one: `models list --provider P`. |
| `TIMEOUT` | The server answered too late. | The engine cancels stalled streams after 120 seconds; retry, or use a smaller model. |
| `CONTEXT_EXCEEDED` / `RUN-CONTEXT-OVERFLOW` | The task input is larger than the model's context. | Split the task, or choose a longer-context model. The context preview in `inspect` shows the token count before you run. |

## Plans

| Code | Meaning | Fix |
| --- | --- | --- |
| `PLAN-INVALID` | The plan failed validation (rules V-01 to V-12). | `plan` prints every rule violation with the task id; fix them in the JSON and re-save. See [plan-schema-reference.md](plan-schema-reference.md). |
| L-rules (`L-01`-`L-09`) | Lint findings: missing dependencies, oversized tasks, unused results, predicted overflow, orphan constraints. | Lint warnings do not block a run; fix them for better results. |

## During a run

| Code | Meaning | Fix |
| --- | --- | --- |
| `HANDOFF-INVALID` | A task result did not follow the handoff format. | Retry (`retry RUN TASK`), edit the task instructions (`edit RUN TASK --instructions ...`), or use a stronger model. |
| `TOOL-CALL-INVALID` | A tool call could not be parsed. | The engine repairs once automatically; if it repeats, turn tools off for that task or rephrase. |
| `LOOP-DETECTED` | The agent repeated itself. | Retry with clearer instructions or another model. |
| `CAP-REACHED` | A token, wall-time, turn or tool-call cap stopped the run. | Raise the cap (run flags or Settings > Runs) and `resume`. |
| `PERMISSION-DENIED` | The task used a tool outside its allowlist. | Grant the tool in the task's `tools` list if you trust the task. |
| `GATE-REQUIRED` | The task needs your approval. | `approve RUN TASK` after reviewing the context preview, or `reject` to stop. |

## Data and events

| Problem | Fix |
| --- | --- |
| `TamperedLogError` on resume | The event chain no longer verifies. `python3 -m las verify RUN` pinpoints where. Restores live in `<data>/backups/`. |
| `DB-NEWER` | The data was written by a newer engine version. Update; your data is untouched. |
| A run disappeared | Retention (`data prune`) or an explicit `delete` removed it. `data status` shows retention. |

## Still stuck?

```bash
python3 -m las diagnostics        # writes a redacted bundle you can attach
```

The bundle contains versions, module state, provider kinds (never keys),
run summaries with goals as hashes only, and chain-verification status.
Review it - the command prints everything it includes before saving.
Nothing is ever sent anywhere by the engine.
