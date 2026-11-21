# Quickstart

Ten minutes from nothing to a finished run. You need Python 3.10-3.12 and a
local model server (LM Studio, llama.cpp, Ollama, vLLM, Unsloth Studio, or
any OpenAI-compatible server).

## 1. Get the engine

```bash
git clone https://github.com/kimpearce888/local-agent-studio
cd local-agent-studio
python3 -m las selftest        # proves the engine works, no server needed
```

The self-test runs a three-task plan against the built-in mock provider and
writes its data under `~/.local-agent-studio/` (override with
`--data-dir DIR` or `LAS_DATA_DIR`).

## 2. Connect your server

```bash
python3 -m las providers detect                 # scans well-known local ports
python3 -m las providers add --name my-server --preset lmstudio
python3 -m las providers test --provider my-server
```

If your server needs a key, point at an environment variable - the key
itself is never stored by the engine:

```bash
export LMSTUDIO_API_KEY=sk-...
python3 -m las providers add --name my-server --preset lmstudio \
    --auth-env env:LMSTUDIO_API_KEY
```

## 3. Plan and run

```bash
python3 -m las plan "Summarize the papers in the workspace into a brief" \
    --provider my-server
python3 -m las run <plan-id>          # or: run builtin-research-report
python3 -m las list                   # watch the status
python3 -m las inspect <run-id>       # task states, attempts, handoffs
```

## 4. Look at the result and keep it

```bash
python3 -m las export <run-id> --format markdown   # report with provenance
python3 -m las templates list                      # reuse plans as templates
```

## Where to go next

- Every command, flag and exit code: [user-guide.md](user-guide.md)
- Something failed: [troubleshooting.md](troubleshooting.md)
- Release channels and support: [release-policy.md](release-policy.md)
- Writing plans by hand: [plan-schema-reference.md](plan-schema-reference.md)
- What the engine does and does not do with your data:
  [security-guide.md](security-guide.md)
