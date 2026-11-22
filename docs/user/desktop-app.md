# The desktop app (GUI)

The desktop app wraps the same engine as the CLI in a local, offline
GUI.  It is pure Python standard library (tkinter) - no Electron, no
bundled browser, no extra dependencies - and every feature of the CLI
works the same way underneath: the same SQLite event log, the same
validation, the same export formats.

Launch it with:

```
studio gui            # or: python -m las gui
studio gui --data ~/.local-agent-studio
```

Everything else in this guide (providers, planning, runs, exports)
applies unchanged; the CLI and GUI read and write the same data
directory and can be used interchangeably on the same runs.

## The layout (spec section 3)

The left navigation has five entries: **Runs**, **Studio**,
**Library**, **Providers** and **Settings**.  Studio is disabled until
a plan exists.  The top bar shows the goal title, a run status chip, a
provider health indicator, a budget meter during runs, and the command
palette button.

## A first run

1. On first launch the **setup wizard** opens: it probes the standard
   local ports (LM Studio 1234, Ollama 11434, llama.cpp 8080, vLLM
   8000, Unsloth Studio 8888), asks for a key only if a server answers
   401, helps you pick planner and worker models with inline
   capability warnings, and ends with the three-task self-test
   (20,000 tokens, 5 minutes, hard caps).  Skip is allowed and
   recorded.
2. **New goal** (Ctrl+N): describe the goal (20 to 20,000 characters,
   with a counter), pick a template and fill its variables, set
   granularity, maximum tasks, stage barriers and the critique pass.
   While the planner works you see each round's reply text and can
   cancel between rounds; a failing repair round shows `Checking the
   plan (repair attempt 2 of 3)`.
3. The **Studio** opens with the plan.  Edit it on the canvas or in
   the list view (see below), check the Constraints and Lint tabs, then
   press **Run** (Ctrl+Enter).  Errors block the run; warnings ask for
   confirmation and list themselves.
4. The **Run view** shows each task's status and elapsed time in the
   left rail, the transcript and handoff of the selected task, the
   workspace files, and the budget meter (amber at 80 percent, stop at
   100).  Pause after this task, pause now (the current attempt aborts
   and restarts on resume), cancel or resume from the header.
   Approval gates and failures open as dialogs with every action the
   specification defines.

## The Studio editor

- **Canvas**: cards in execution order, six per row, with sequence
  edges (thin, arrowed) and data-dependency edges (accent, dashed,
  labelled with the handoff they carry).  Drag to reorder - a drop that
  would break a dependency is refused with the reason; drag from a
  card's output port to a later card to add a data dependency.
- **List view** (Ctrl+Shift+L): the accessible equivalent - an ordered
  table where every row carries the full card name, for example
  `Task 3 of 8: Compare suppliers, succeeded, uses t1 and t2, risk
  flags 1 of 3`.  Set it as your default in Settings → Accessibility.
- **Inspector** (right, collapsible): eight tabs - Task, Inputs,
  Constraints, Model, Tools, Limits, Context (the exact messages the
  model will receive, with per-block token counts), Result.
- **Bottom dock**: all plan constraints, lint findings with
  click-to-focus, and the run log.
- Undo/redo (Ctrl+Z / Ctrl+Shift+Z) covers every edit; plans autosave
  continuously (`Saved 14:32` in the toolbar).

## Keyboard (spec section 9)

| Action | Windows and Linux | macOS |
| --- | --- | --- |
| Command palette | `Ctrl+K` | `Cmd+K` |
| New goal | `Ctrl+N` | `Cmd+N` |
| Run | `Ctrl+Enter` | `Cmd+Return` |
| Pause after this task | `Ctrl+.` | `Cmd+.` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` | `Cmd+Z` / `Cmd+Shift+Z` |
| Move task earlier / later | `Alt+↑` / `Alt+↓` | `Option+↑` / `Option+↓` |
| Open inspector | `Enter` | `Return` |
| Switch Canvas / List | `Ctrl+Shift+L` | `Cmd+Shift+L` |
| Next region | `F6` | `F6` |
| Show shortcuts | `Ctrl+/` | `Cmd+/` |

Every shortcut can be changed in Settings → General → Shortcut editor,
and no action is reachable only by keyboard.

## Themes and accessibility

Settings → General switches light, dark and high-contrast themes;
Settings → Accessibility sets reduce motion, text size (100 to 200
percent) and the default editor view.  Status is always an icon plus
text, never colour alone, and the three palettes are checked against
WCAG contrast ratios in the test suite.  Dialogs trap focus and return
it to the control that opened them; F6 cycles the main regions.

## Honest platform notes

These are the places where the desktop integration is thinner than
the specification's eventual target, and what happens instead:

- **Keys** are stored by environment reference (`env:NAME`), as in the
  CLI, not yet in the OS keychain; the Replace-key dialog sets the
  environment variable for the session.
- **Theme: system** follows the `LAS_THEME` environment variable
  (`dark` or `light`, default light) because tkinter exposes no OS
  preference API.
- **Run notifications** appear in the status line and the Runs chip
  (`Needs you`), not yet as OS notifications.
- **Model streaming**: the transcript shows each round's collected
  text (per-round updates), not per-token streaming, because the
  provider adapters collect each reply before validation.
- Installers, signed updates and automatic update checks (DST-1)
  are not part of the source release; run from source or package it
  for your platform with your usual tooling.

## The Rust desktop (egui shell, 0.4.0)

A second, independent desktop front end lives in `rust/` (crates
`las-core` and `las-desktop`).  It is the agent-first product shell:
egui/eframe, pure Rust, no system web stack, packaged as the .deb and
AppImage artifacts described in `release-policy.md`.  The Python GUI
above remains the UX reference for the plan/run workflow; the Rust
shell is the multi-agent workspace.

All 14 navigation areas are live, each backed by the real engine
(SQLite store in `las-core`), never mock data:

- **Dashboard** - agent/inbox counts straight from the store.
- **Agents** - profiles, enable/disable, tool grants at creation.
- **Inbox & Messages** - per-agent inbox with queue/interrupt busy
  modes; delivery is the runtime's real path.
- **Collaboration Channels** - shared channels with comm-rule
  enforcement.
- **Agent Teams / Network** - default-deny agent-to-agent
  communication rules (message / call / delegate).
- **Sessions & Memory** - transcripts and the per-agent memory
  store.
- **Providers & Models** - endpoint registry (keys stay
  `env:NAME` references, never values).
- **Tools & Integrations** - the grant matrix with per-tool modes
  (always / ask once / ask each / never) and the approval gate.
- **Projects** - projects grouping agents; membership is
  organization, never a permission bypass.
- **Artifacts & Files** - read-only browsing of each agent's
  workspace jail (the same files the tool engine writes).
- **Tasks & Delegations** - tasks are tracked AND delivered to the
  agent's real inbox.
- **Workflow Studio** - plan editing that exports plan schema v1
  JSON runnable by `python3 -m las run <file>`.
- **Activity & History** - the audited event trail.
- **Settings & Diagnostics** - version, data directory, SQLite
  quick_check, row counts, provider endpoints.

### The tool engine and the approval gate

Inside a model turn, a reply may carry `TOOL_CALL` JSON lines.  Every
call passes the permission broker in the spec 5.9 order: an explicit
grant (nothing is granted implicitly), argument and size limits
(5 MB per file), workspace-jail containment (path traversal and
symlinked directories are refused), and the per-turn call budget.
File tools (`read_file`, `write_file`, `list_files`) run inside the
agent's own workspace; `web_fetch` runs behind a domain allowlist
with resolved-IP SSRF checks (private/loopback targets are refused),
manually re-validated redirects, a text-only MIME gate and 1 MB /
10 s caps.  Every decision is audited as `tool.called` or
`tool.denied`; the Rule-of-Two capability flags (untrusted input,
sensitive access, external effect) are computed from the grants by
code, never from what the model says.

A grant in mode *ask once* or *ask each* defers the call: the model
sees "deferred: this tool needs user approval first", the request
appears in Tools & Integrations > Pending approvals, and approving
it either upgrades the grant (ask once) or pays for exactly one more
use (ask each).  Nothing executes while waiting.

In this sandbox the window cannot open (no X11 libraries); the UI is
verified headlessly with egui_kittest - the same draw code that runs
on screen, asserted against the real store (see
`rust/crates/las-desktop/tests/ui.rs`).

### Sessions, rollover segments and the MCP client

An agent's conversation is one logical thread that may span several
session SEGMENTS.  When a transcript outgrows the agent's policy
(`rollover_max_messages` / `rollover_max_tokens` in the profile
params; defaults keep a long margin below the context budget), the
runtime rolls over BEFORE the next turn: the prior segment stays
complete and readable (its last row is the closing note), and the
next segment opens with a structured continuity summary - pinned
instructions and unresolved requirements verbatim, decisions,
references and artifacts, and the recent tail.  The Agents screen's
session combo lists every segment with its status (`open`,
`rolled_over`, `closed`), so prior segments are inspectable;
resuming from an earlier one branches a NEW segment seeded from it
(history is append-only and never rewritten).

MCP (Model Context Protocol) servers extend an agent's tools.  A
server is configured with a command line and an id; it stays
DISABLED until you approve the connection (an imported config never
implies approval).  Once approved, the agent can call its tools
under grants `mcp:<server>` (the whole server) or
`mcp:<server>:<tool>`, through the same broker and approval gate as
the built-in tools, subject to the server's own fail-closed
`allow_tools` list.  Tool results are always labelled UNTRUSTED
content with the origin server named, and connections, restarts and
calls are audited like every other tool decision.  The transport is
stdio (a local child process speaking JSON-RPC); Streamable HTTP
for remote servers is not implemented yet.
