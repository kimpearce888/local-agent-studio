# Security guide

What the engine guarantees, where its boundaries are, and what it expects
from you. The engine is local-first: every plan, run, transcript and
export lives under your data directory; nothing is sent anywhere except to
the model servers you configure.

## Keys and credentials

- The engine never stores API keys. A provider profile references an
  environment variable (`--auth-env env:MY_KEY`); the key stays in your
  environment or OS keychain.
- Diagnostics bundles and exports redact by default: keys never appear,
  and with `--redact-prompts` the export replaces prompt text with
  lengths and hashes.

## The workspace jail (TLS-1)

Every run gets a workspace directory. File tools (`list_files`,
`read_file`, `write_file`, `search_files`, `patch_file`) resolve symlinks
and refuse any path that leaves it. The path-traversal suite in
`tests/test_workspace.py` proves it; run it any time with
`python3 -m unittest tests.test_workspace`.

## The Rule of Two (BLD-4, spec section 12)

A task is profiled on three properties: untrusted input, sensitive access,
external effect. The linter blocks any task that has all three without an
approval gate in front of it (L-09). Gates show the exact context preview
and previous output before you decide.

## Tool permissions (TLS-2)

Only the five workspace file tools are on by default. Per-task allowlists
open more; every call and every denial is an event in the audit log.

## Tamper-evident history (RUN-5)

Every event is hash-chained: each event's hash covers the previous hash.
`python3 -m las verify RUN` walks the chain and reports the first
divergence. Automatic backups are written to `<data>/backups/` before
schema changes.

## Network

The engine opens connections only to providers you configured, and only
when a run needs them. Telemetry does not exist (SET-3); the loopback API
listens on 127.0.0.1 only, requires the per-launch token from
`<data>/api-token`, and rejects cross-origin browser requests.

## Updates and supply chain (SET-4)

The engine itself checks nothing automatically. Update only from the
signed release channel you choose; installer signing/notarization is part
of the desktop packaging (see PROGRESS.md for its status).

## Reporting a problem safely

`python3 -m las diagnostics` writes a bundle you can review before
sharing. It lists its contents first: goals as length + SHA-256, provider
ids and kinds (never keys, never URLs with credentials), module state,
run summaries, chain-verification status. Open the JSON and check it
before you attach it anywhere.
