# HANDOVER

Last updated: 2026-11-21 (mandate re-baseline session)

## What this project is now

The delivery mandate (`upload/Pasted Content_1795301785623.txt`,
section 2) redefines the product: a **local-first, independent
multi-agent desktop environment** - persistent agents with identity,
sessions, memory, inboxes and permissions, communicating through
durable mailboxes, sharing local model infrastructure through a fair
scheduler, supervised from one desktop application. The previous
product definition (goal → plan → sequential task chain, the repo
PRD/spec) becomes a subsystem (workflows) of the new product
(mandate section 11). The mandate takes precedence over the old PRD.

The stack is Rust-first (mandate 4.1): the production engine is Rust;
the Python engine (`las/`, v0.3.0, 313 tests green) is the behavioral
reference and migration source, kept runnable for parity tests.

## Where things stand

0. **The Rust product core exists and is tested** (this session,
   commits 828968e + this one): las-core with storage/agents/
   communication/runtime/scheduler (36 unit tests), acceptance
   scenarios 19.1 + 19.2 steps 1-6, and the egui desktop shell with
   Dashboard/Agents/Inbox live (5 kittest headless UI tests, release
   build green). 43 Rust tests total. See BUILD_STATUS.md for the
   module-by-module breakdown and the exact next action.
1. **Reference engine hardened this session** (audit items 6, 8, 11,
   18 fixed with regression tests in `tests/test_mandate_fixes.py`):
   real token counting with honestly-labelled estimates; supporting
   model calls accounted (`support.usage` events, included in caps);
   empty command allowlist denies; resume restores the frozen
   execution configuration recorded in `run.created`.
2. **Desktop stack decided with evidence** (ADRs 002/003,
   BUILD_STATUS.md): Tauri 2 is blocked in this build environment
   (no webkit2gtk/gtk pkg-config entries, no passwordless sudo). The
   product shell is egui/eframe - fully Rust, zero system deps on the
   user's Ubuntu 22.04 target. In this sandbox the window cannot open
   (`libxkbcommon-x11` missing), so the UI is verified headlessly with
   egui_kittest; the binary compiles and the same UI code runs on
   screen outside the sandbox.
3. **Rust workspace scaffolded** at `rust/` (`las-core`,
   `las-desktop`); the egui shell builds; kittest added.

## What is NOT done (do not claim otherwise)

- Tool execution v1 is IN and COMPLETE for the P0 scope: broker,
  jailed executors, audit, the TOOL_CALL loop, Rule-of-Two flags,
  the approval gate (ask once / ask each with a pending-approvals
  queue on the Tools screen) and the hardened web fetch (SSRF
  resolved-IP checks, per-hop re-validated redirects, MIME gate -
  audit item 12 fixed).  Still P1, not release-blocking: shell/code
  tools behind Landlock (audit item 10), MCP client, artifacts as
  versioned first-class entities (the Artifacts screen browses the
  real workspace files today), a real tokenizer for context
  assembly (the chars/4 approximation is deliberate).  Packaging:
  .deb and AppImage are rebuilt and STRUCTURALLY VERIFIED by
  `scripts/make_deb.py` / `scripts/make_appimage.py`; installing
  them needs root/FUSE on the target machine - environment-blocked
  here, recorded honestly.  Versions unified at 0.4.0.  The
  14-area desktop shell is complete (14/14 live, kittest-verified).
- Landlock sandboxing for run_command (audit item 10), MCP
  Streamable HTTP transport and dynamic tool-schema injection (the
  stdio MCP client core is IN: configs, approval-before-connection,
  allowlists, grants, audit, untrusted labelling), recursive module
  registry (item 14).  Context rollover is IN (session segments,
  continuity summaries, restore); a model-generated summary layer
  would ride the provider adapters when a server is configured.
- Real-server capability tests and real-model benchmark (items 19,
  20) - blocked by the sandbox; harnesses must ship for the user.
- Real-server capability tests and the real-model benchmark (audit
  items 19, 20) - blocked by this sandbox having no model server;
  the harnesses are shipped for the user's machine.

## How to run everything

```bash
# Python reference engine + CLI + GUI (behavioral reference)
python3 -m unittest discover -s tests     # full suite (GUI classes skip headless)
Xvfb :99 -screen 0 1280x820x24 &          # then: DISPLAY=:99 python3 -m unittest tests.test_gui
python3 -m las selftest                   # end-to-end against the mock

# Rust product engine
source "$HOME/.cargo/env"
cd rust && cargo test --workspace         # las-core unit + parity, desktop kittest
cd rust && cargo build --release
```

The sandbox recycles background processes between turns: restart Xvfb
before GUI tests; Rust lives in `~/.cargo`.

## How to continue

- Read `BUILD_STATUS.md` first (audit table with evidence, decisions,
  exact next action) and this file. Work in the repository, commit
  reviewable increments, update both files after each meaningful task
  (mandate section 22).
- Python fixes go with regression tests; Rust modules go with unit
  tests and, where behavior mirrors the Python engine, parity tests.
- Never mark a capability complete without a test that exercises it;
  record environment-blocked verifications explicitly instead.

## Key paths

- `las/` Python reference engine, CLI, tkinter GUI (UX reference).
- `rust/crates/las-core` Rust engine (in progress).
- `rust/crates/las-desktop` egui shell + kittest headless UI tests.
- `docs/adr/` decision records.
- `tests/test_mandate_fixes.py` audit-item regression tests.
- `BUILD_STATUS.md` audit table, decisions, exact next action.
