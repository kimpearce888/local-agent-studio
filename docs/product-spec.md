# Local Agent Studio: Product Specification Document

Version 1.0 draft · November 20, 2026 · Based on the Product Requirements Document, version 2.0 · Status: ready for design and engineering review

## 1. Purpose and conventions

The Product Requirements Document (PRD) says what Local Agent Studio must achieve and why. This specification says exactly how it behaves: screens, rules, data, interfaces and messages, in enough detail for design, engineering, QA and documentation to work in parallel. Where the two disagree, the PRD wins and this document is corrected.

- Requirement IDs such as RUN-5 refer to the PRD. Section 11 maps each requirement to where it is specified here.
- Shall means required for 1.0, should means recommended. A value marked default is a starting value that the Phase 0 benchmark confirms or changes; users can change it in Settings unless it is marked fixed.
- Text in code style is a literal value, field name or user-facing message.
- Scope: the desktop app for Windows, macOS and Linux, the local engine and the CLI. The PRD's non-goals stay out of scope.

## 2. The product in one flow

A user connects a local model server and enters a goal. A planner proposes a plan of small tasks with binding constraints. The user reviews and edits the plan on a canvas, checks a preflight report and a context preview, and starts the run. The scheduler runs each task as a fresh session, one after another. The user steps in at approval gates and failures, then reviews the results and exports a report.

| Step | What happens | Screen |
| --- | --- | --- |
| 1 | Connect and test a model server | S1 Setup, S9 Providers |
| 2 | Write the goal, choose planning options and models | S3 New goal |
| 3 | The planner proposes tasks and binding constraints | S3, then S4 |
| 4 | The user reviews and edits the plan | S4 Studio |
| 5 | Preflight checks and context preview | S4, S5 |
| 6 | The run executes task by task | S6 Run view |
| 7 | The user resolves gates and failures | S7, S8 |
| 8 | Review, export, reuse as a template | S6, S12, S10 |

## 3. Information architecture

The left navigation has five entries: Runs, Studio, Library, Providers and Settings. Studio shows the current plan and is disabled until a plan exists. The top bar always shows the goal title, a run status chip, a provider health indicator (a coloured dot with a text label), a budget meter during runs, and a command palette button (`Ctrl+K` on Windows and Linux, `Cmd+K` on macOS).

| ID | Screen | Purpose | Reached from |
| --- | --- | --- | --- |
| S1 | Setup wizard | Connect a server, choose models, run a self-test | First launch, Providers > Add |
| S2 | Runs | History and entry point | Navigation |
| S3 | New goal | Compose a goal and planning options | Runs > New goal, command palette |
| S4 | Studio | Canvas, list view, inspector, preflight | After planning, Runs > Open |
| S5 | Context preview | Exact input of one task | S4 inspector, S7 |
| S6 | Run view | Live execution, transcripts, handoffs, files | Run start, Runs > Open |
| S7 | Approval gate | Approve, edit or reject at a gate | S6 |
| S8 | Failure panel | Retry, skip, edit or stop after a failure | S6 |
| S9 | Providers | Profiles, connection tests, model list | Navigation |
| S10 | Library | Templates and role profiles | Navigation |
| S11 | Settings | Preferences, data, privacy, updates | Navigation |
| S12 | Export | Report and bundle export | S6, S2 |
| S13 | Diagnostics | Redacted bundle for bug reports | Settings > Advanced |

## 4. Screen specifications

Each specification lists layout, behavior rules and states. Every screen supports the global keyboard rules in section 9.

### S1 Setup wizard (DST-2)

Five steps with a progress indicator; Back is always available.

1. Welcome. States that nothing leaves the machine except requests to the servers the user chooses.
2. Detect servers. Probes localhost ports 1234 (LM Studio), 11434 (Ollama), 8080 (llama.cpp server), and 8000 and 8888 (vLLM and Unsloth Studio defaults). A probe is one unauthenticated model-list request with a 1 second timeout; it sends no credentials and follows no redirects. Results list each found server with a type guess and status. Add manually takes a name and a base URL.
3. Credentials. Shown only for servers that answered 401. It explains where to create the key (LM Studio: server settings, API tokens; Unsloth Studio: Settings, API) and stores it in the OS keychain. A wrong key shows `The server rejected this key. Check that it was copied completely.`
4. Models. Planner and worker pickers list models with capability results (streaming, tool calling, JSON schema, context length) as text with icons. Warnings: context under 8,192 tokens (`Tasks will be limited to short inputs.`) and a model reported under 7B parameters as planner (`Small models may need several repair rounds when planning.`).
5. Self-test. Runs a built-in three-task plan with hard caps (20,000 tokens, 5 minutes). Each step shows pass or fail with the reason. Finish is enabled after a pass; Skip is allowed and recorded.

Empty state (no server found): start instructions for each supported server, Retry and Add manually.

### S2 Runs (OBS-1)

A header with the primary New goal button, and a filter bar with search text, status, model and date range. Table columns: title (first 60 characters of the goal), status chip, tasks done of total, duration, planner and worker models, tokens, started. Row actions: Open, Resume (paused or failed runs), Duplicate plan, Export, Delete. Multi-select enables bulk delete with a dialog that states the count. Any column sorts; 50 rows per page. Empty state: `No runs yet. Describe a goal to create your first plan.` with a New goal button.

### S3 New goal (PLN-4, PLN-5)

| Field | Rules |
| --- | --- |
| Goal | Required, multi-line, 20 to 20,000 characters, with a counter |
| Start from template | Optional; fills variables through a form |
| Attachments (P1) | Files or folders, shown to the planner as a manifest only |
| Granularity | Coarse (about 3 to 6 tasks), Normal (about 6 to 15, default), Fine (about 15 to 30) |
| Maximum tasks | Default 30; the interface allows 1 to 100 |
| Stage barriers | Off by default; when on, the planner adds a collector after each stage |
| Critique pass | Off by default; a second planner session reviews the plan |
| Planner model, worker model | Default to the last used; capability warnings appear inline |

Plan runs the planner. While it works the screen shows `Planning...` with the streamed text and a Cancel button; after a failed validation it shows `Checking the plan (repair attempt 2 of 3)`. On success S4 opens with the plan, and the extracted constraints are highlighted against the goal text. Start with a blank plan opens S4 empty. If the planner cannot produce a valid plan, S4 opens with the partial plan and the errors in the Lint tab.

### S4 Studio (BLD-1 to BLD-8)

Layout: toolbar on top, canvas in the centre, inspector on the right (360 px, collapsible), and a bottom dock with Constraints, Lint and Log tabs.

Toolbar: Undo, Redo, Add (Task, Collector, Approval gate, Input, Output, Note), Auto-layout, zoom controls and minimap toggle, a view switch (Canvas or List), Validate, Run (primary), and a Plan menu with Save as template, Export JSON, Version history and Regenerate. Autosave is continuous and the toolbar shows `Saved` with a time.

Canvas rules:

- Nodes appear in execution order, left to right, wrapping after six per row. Each card shows the order number, title (two lines at most), type icon, a status icon during runs, the capability badge, a tool count, and a model chip when overridden.
- Sequence edges are thin neutral lines with arrows. Data dependencies are accent lines labelled with the handoff they carry. Selecting a node highlights its edges and dims the rest.
- Click selects, double-click renames inline, Delete removes (with a confirm dialog if other tasks depend on it). Shift-click and drag-select choose several. Copy and paste duplicate with new ids.
- Dragging a card shows a drop marker. A drop that would place a task before one it depends on is refused with the tooltip `t5 needs t3, so it cannot run before it.` Dragging from an output port to a later task's input port adds a data dependency; dragging to an earlier task is refused.
- Keyboard: arrow keys move focus between cards, `Enter` opens the inspector, `Alt+Up` and `Alt+Down` move the selected task earlier or later, and `L` links the selected task to a later one through a picker. Every action also exists in List view and the context menu.

List view is an ordered table (order, title, type, needs, model, badge, status) with Move up, Move down and a drag handle. It is the accessible equivalent of the canvas.

Capability badge: three indicators labelled `A untrusted input`, `B sensitive access` and `C external effect`, each filled when true, with a text summary such as `2 of 3` and a tooltip explaining why each is set. A task with `3 of 3` shows a red `Needs a gate` marker.

Inspector tabs:

| Tab | Contents |
| --- | --- |
| Task | Title (1 to 80 characters), instructions (20 to 4,000 characters, `{{variable}}` autocomplete), expected output (optional), acceptance criteria (P1) |
| Inputs | Checklist of earlier tasks (needs); each row previews that task's handoff summary once it exists |
| Constraints | Binding constraints that apply to this task, with an Add control; inherited ones are read-only and labelled with their origin |
| Model | Role (planner, worker, collector), model override, temperature, top\_p, max tokens, seed, stop sequences, context length |
| Tools | Per-tool toggles with risk labels and the resulting capability flags |
| Limits | Retries (default 2), timeout, token budget, maximum turns, maximum tool calls |
| Context | Button `Preview context` (opens S5) and the current token estimate |
| Result | Appears in runs: the handoff and a link to the transcript |

Bottom dock: Constraints lists every plan-level constraint (id, text, prerequisite, authority, fallback, tasks it applies to). Lint lists findings with severity and a click-to-focus link. Log shows run events in order.

Preflight: pressing Run executes the linter. Errors block the run; warnings show a confirmation that lists them. Empty state: `Add your first task, or go back and press Plan.`

### S5 Context preview (BLD-3, RUN-1)

A dialog with three tabs. As sent shows the exact messages the model will receive, with section labels. Structure shows a stacked bar and a table of token counts for reserved output, system block, binding constraints, goal, instructions, handoffs, workspace manifest and tool definitions. Warnings lists predicted overflow (above 80 percent of context) and any compaction that would apply. Counts are exact when the server has a token-count endpoint and are labelled `estimated` otherwise. Actions: Copy, Close.

### S6 Run view (RUN-5, RUN-6, OBS-2, OBS-3)

Left rail: the task list with status icon, text status and elapsed time. Main area tabs: Overview (timeline, progress, tokens per second, time to first token), Transcript (the selected task's events), Handoff (structured fields) and Files (workspace tree with preview). Header controls: Pause after this task, Pause now (aborts the current attempt, which restarts on resume), Cancel run and Resume.

Transcript behavior: streaming text with an auto-scroll toggle; tool calls as collapsible rows with arguments and results; reasoning blocks collapsed under `Reasoning (not passed on)`; untrusted content marked with an `Untrusted source` label.

Budget meter: tokens and elapsed time against caps. At 80 percent it turns amber with a warning icon and the text `80% of the token cap`; at 100 percent the run stops.

Completion card: total time, tokens and tasks, a link to the Output node's result, Export, and Save as template. A run that needs input shows the chip `Needs you` and, when permitted, an operating-system notification.

### S7 Approval gate (HIL-1 to HIL-3)

Shown in S6 when the run waits at a gate. It shows why the gate exists (`Added by you` or `Required: this task has all three risk flags`), the task's context preview, the previous handoff (editable), the tools the task will have, and a diff of anything changed since the plan was last approved. Actions: Approve; Edit handoff and approve (stores a revision with a required reason); Reject (choose stop the run, skip the task, or edit the instructions). The approval record stores the time and a hash of exactly what was shown.

### S8 Failure panel (RUN-7)

Shown when a task ends in failure. It shows the reason code and a plain explanation, attempts used, the last error, an excerpt of the last assistant message, and suggested fixes. Actions: Retry (instructions can be edited first), Retry with another model, Skip (if other tasks depend on it, the user chooses to stop or to continue with them marked `Blocked by skip`), Edit task and rerun from here, and Stop run. The default policy is Ask; Settings can change it to Stop or Skip.

### S9 Providers (PRV-1 to PRV-4)

A list of profiles on the left, each with a health label (`Connected`, `Key needed`, `Unreachable`). Detail on the right: name, base URL, key (masked, with Replace), extra headers, timeouts and Test connection. The test table shows Pass, Fail or Unknown with an explanation for: reachable, authenticated, models listed, streaming, tool calling, JSON-schema output, token counting and router mode. A models table lists id, loaded state, context length, capabilities and role tags, with Refresh; Preload and Unload appear when the server supports them (P1). Delete asks for confirmation and removes the key from the keychain.

### S10 Library (LIB-1, LIB-2)

Two tabs. Templates: cards with name, description, task count and variables, with Use, Duplicate, Edit, Export and Delete; built-ins cannot be deleted but can be duplicated. Role profiles: a form for name, system prompt, parameters, tool allowlist and default model.

### S11 Settings (SET-1 to SET-4)

| Section | Contents |
| --- | --- |
| General | Theme (light, dark, system), language (English), shortcut editor |
| Runs | Default caps (section 5.8), failure policy, gate defaults, queued-run behavior (one at a time, fixed) |
| Data | Location, retention (keep forever, 90 days, 30 days), export all data, delete all data |
| Privacy | Telemetry (off, fixed in 1.0), crash reports (opt-in, P1), a network log showing every connection the app made |
| Updates | Channel (stable or beta), Check now, automatic updates (off by default) |
| Accessibility | Reduce motion, high contrast, text size |
| Advanced | Log level, diagnostics (S13), engine details, developer API toggle (P1) |

### S12 Export (OBS-4)

Format: Markdown report or JSON bundle. Include: plan, handoffs, transcripts, workspace files. Redact prompt text is off by default for reports and on for diagnostics. The AI-generated notice is on by default in Markdown reports and states the run id and the models used. A summary shows what will be included and its size before saving.

### S13 Diagnostics (OBS-7)

Lists every item that will be included, with redaction applied (keys removed, prompt text replaced by lengths and hashes), and lets the user open the file before saving. Nothing is sent anywhere.

## 5. Behavior specifications

### 5.1 Planner contract (PLN-1 to PLN-5)

The planner runs as its own session. Its input is the fixed system prompt below, the goal, the planning options and, in P1, the attachment manifest. Its output is one JSON object that conforms to plan schema v1 (section 6.1).

System prompt rules (fixed text, versioned with the app):

1. Split the goal into tasks that one agent can finish in one session within the stated context length.
2. Make each task self-contained: it sees only the goal, the binding constraints and the results of tasks listed in needs.
3. Dependencies may name only earlier tasks.
4. Copy every hard requirement in the goal (words such as must, never, only, before, requires approval) into constraints exactly as written, one per requirement, and add a prerequisite, an authority and a fallback where the goal implies them.
5. Add a collector task whenever results from several tasks must be combined, and a final task that produces the deliverable.
6. Reply with JSON only.

When the model supports JSON-schema output the schema is sent with the request. Otherwise the prompt ends with the schema as text and the reply is parsed leniently, with code fences and leading text removed.

Repair loop: the validation rules in section 5.2 run on every reply. On failure the engine sends the original request, the invalid reply and a list of rule ids with their messages, and asks for corrected JSON only, up to three repair rounds. After the third failure the engine returns the longest valid prefix of the plan, marks the first failing task, and shows the errors in S4.

Critique pass (optional): a second session receives the goal and plan and returns at most five issues (missing step, oversized task, ambiguous instruction, dropped constraint, missing collector). They appear in the Lint tab as `Suggestion` items. The planner never edits the plan by itself.

### 5.2 Plan validation rules

| Rule | Check | Message |
| --- | --- | --- |
| V-01 | The JSON parses and matches schema v1 | `The plan is not in the expected format.` |
| V-02 | Task ids are unique and match `^[a-z][a-z0-9_-]{0,31}$` | `Task id t3 is used twice.` |
| V-03 | needs names only earlier tasks | `t5 needs t3, which comes later.` |
| V-04 | No cycles (implied by V-03, rechecked on import) | `The plan contains a loop: t2, t4, t2.` |
| V-05 | Title is 1 to 80 characters; instructions are 20 to 4,000 | `t4 has no instructions.` |
| V-06 | Task count is between 1 and the maximum (default 30, hard limit 500) | `The plan has 41 tasks; the limit is 30.` |
| V-07 | At most 8 needs and 12 constraints per task | `t6 lists more than 8 inputs.` |
| V-08 | The last task is the only task no other task needs | `The last task must produce the final result.` |
| V-09 | Every constraint has an id, text and state; ids are unique | `Constraint c2 has no text.` |
| V-10 | Constraint scopes name existing tasks | `Constraint c4 applies to t9, which does not exist.` |
| V-11 | Every tool named by a task exists | `t3 asks for a tool that does not exist: browse.` |
| V-12 | Every {{variable}} has a value or a default | `The variable topic has no value.` |

### 5.3 Lint rules (BLD-7)

| Rule | Severity | Condition | Message |
| --- | --- | --- | --- |
| L-01 | Error | A task has capability flags A, B and C and no gate before it | `t6 can read untrusted text, reach private files and send data out. Add an approval gate before it.` |
| L-02 | Error | Predicted input does not fit the context even after the full compaction ladder | `t7 will not fit in the model's context, even after compaction. Split the task.` |
| L-03 | Warning | Predicted input is above 80 percent of context before compaction | `t7 uses 86% of the context.` |
| L-04 | Warning | A task's result is used by no later task and it is not the last task | `t4's result is not used by any later task.` |
| L-05 | Warning | A plan constraint applies to no task | `Constraint c3 is not carried by any task.` |
| L-06 | Warning | Instructions are longer than 2,500 characters | `t2 is long. Consider splitting it.` |
| L-07 | Warning | Tools are enabled but the model cannot call tools | `This model cannot call tools; the text tool protocol will be used.` |
| L-08 | Info | A collector has fewer than two inputs | `t5 is a collector with one input.` |
| L-09 | Error | The provider for a task's model is not reachable | `The server for t3's model is not reachable.` |

### 5.4 Context assembly (RUN-1, RUN-9)

Each task attempt builds a new message list in this fixed order and includes nothing else.

1. System block: the role profile's prompt, then fixed protocol text (how to use tools, how to finish with a handoff, how to treat untrusted content).
2. Binding constraints block, labelled `BINDING CONSTRAINTS`: every constraint that applies to the task, copied verbatim with state, prerequisite, authority and fallback.
3. Goal.
4. Task block: id, title, instructions, expected output.
5. Inputs: for each needed task, its handoff (status, summary, key facts, decisions, open issues) in structured form and labelled with the source task. Constraints already in block 2 are not repeated.
6. Workspace manifest: paths, sizes and hashes, up to 200 entries.
7. Tool definitions (native) or the text tool protocol.

Untrusted content (file text, web pages, tool output) appears only in tool results or in fenced blocks labelled `UNTRUSTED`, never inside blocks 1 to 4.

Token budget. Reserved output R is the task's max tokens (default 4,096), capped at 25 percent of the context. Safety margin M is 5 percent. Available input is context minus R minus M. If the assembled input exceeds it, the compaction ladder runs in order until it fits:

1. Replace each handoff summary with a short one (at most 120 words) written by a summarizer session, and log a compaction event with before and after sizes.
2. Move all but the 20 most relevant key facts into a workspace file and leave a pointer the task can open with the file tool. Facts cited by the task's instructions rank first, then the most recent.
3. Trim the workspace manifest to 50 entries.

Never compacted: binding constraints, the task block, the goal, status fields and the system block. If the ladder cannot make the input fit, the attempt does not start. The run stops with `RUN-CONTEXT-OVERFLOW` and the failure panel suggests splitting the task.

Token counting uses the server's count endpoint when it exists (Unsloth Studio offers one), else the model family's tokenizer when known, else characters divided by 3.5 with a 15 percent safety factor, labelled `estimated`.

### 5.5 Handoff production and validation (RUN-3, HIL-3)

A task ends when the model sends a final message with no tool call. The protocol text tells it to end with a `<handoff>` block containing JSON that follows the handoff schema (section 6.2). The engine always stores the raw final text, then:

1. extracts and parses the block;
2. validates it with the rules below;
3. on failure sends one repair message in the same session that lists the failed rules;
4. if it still fails, ends the attempt with `HANDOFF-INVALID`.

| Rule | Check |
| --- | --- |
| H-01 | The block matches the handoff schema |
| H-02 | status is done, partial or blocked |
| H-03 | Every constraint id in the task's constraints block appears in binding\_constraints, and its text matches by SHA-256 of the whitespace-normalized text |
| H-04 | No new constraint reuses an existing id |
| H-05 | A constraint changes to resolved only with evidence from its named authority (a user record, a named verification task, or a named tool); the model's own claim is rejected |
| H-06 | Every key fact cites a source: a message id, a tool call id or a file path |
| H-07 | Every deliverable path exists in the workspace and its hash matches |
| H-08 | The summary is at most 400 words |

### 5.6 Scheduler (RUN-2, RUN-5, RUN-6)

1. On start, write `run.created`, fix the execution order to the plan order, mark every task pending, and consider only the first task in order that is not succeeded or skipped.
2. If a gate sits before it, or rule L-01 applies, set the task to awaiting approval and wait.
3. Build the context (section 5.4), write `attempt.started` with the full request, and call the provider adapter with streaming on.
4. Stream events to the log. For each tool call, run the permission broker (section 5.9), execute, append the result, and continue until a final message, a cap or a failure.
5. Validate the handoff (section 5.5). On success write `handoff.recorded` and `task.succeeded`; on failure apply section 5.7.
6. Repeat until no task remains, then write `run.succeeded`.

Controls: Pause after this task stops the scheduler before step 2 of the next task. Pause now aborts the stream, marks the attempt `aborted` and returns the task to ready. Cancel aborts and marks all remaining tasks cancelled. After a restart the engine replays the log; an attempt that started but never ended is marked aborted and the task returns to ready, and no partial handoff is used. Before retrying, the engine lists any completed tool calls that changed anything outside the workspace and asks for confirmation. Queued runs wait their turn in creation order; only one run is active.

### 5.7 Failure handling (RUN-7)

| Failure | Detection | Automatic action | Then |
| --- | --- | --- | --- |
| Server or network error | Connection error or 5xx | Retry after 2, 8 and 30 seconds, up to the task's retries | Failure panel |
| Stall or timeout | No token for 120 seconds, or the task timeout | Abort the attempt and retry once | Failure panel |
| Bad handoff | Rules H-01 to H-08 | One repair message | `HANDOFF-INVALID` |
| Bad tool call | Arguments fail to parse or validate | One repair message | `TOOL-CALL-INVALID` |
| Context overflow | Ladder fails, or the server rejects the size | No retry | `RUN-CONTEXT-OVERFLOW` |
| Loop | The same tool call with the same arguments three times in a row, or four identical assistant messages | Abort the attempt; one retry with temperature raised by 0.2 | `LOOP-DETECTED` |
| Cap reached | Token, time, turn or tool-call cap | Stop the run, no retry | `CAP-REACHED` |
| Permission denied | The broker denies a tool; a second denial in one attempt | Return the denial to the model once | `PERMISSION-DENIED` |
| Model missing | The server no longer lists the model | No retry | `MODEL-UNAVAILABLE` |

Failure policy options are Stop, Skip and Ask (default). Skip marks tasks that depend on the skipped one as `Blocked by skip`, and they are skipped too unless the user chooses to continue with them.

### 5.8 Budgets and defaults (RUN-8, RUN-9)

| Setting | Default | Range or note |
| --- | --- | --- |
| Max output tokens per attempt | 4,096 | 256 up to the model's limit |
| Wall-time cap per task | 30 minutes | 1 minute to 4 hours |
| Stall timeout (no tokens) | 120 seconds | 10 to 900 seconds |
| Turns per task | 25 | 1 to 200 |
| Tool calls per task | 100 | 1 to 1,000 |
| Retries per task | 2 | 0 to 5 |
| Planner repair rounds | 3 | Fixed |
| Token cap per run | 2,000,000 | 10,000 to 50,000,000; there is no unlimited setting |
| Wall-time cap per run | 8 hours | 5 minutes to 72 hours |
| Tasks per plan | 30 | Hard limit 500 |
| Workspace file size | 5 MB per file | 200 MB per run |
| Context warning | 80 percent | Fixed |
| Compaction summary size | 120 words per handoff | Fixed |

### 5.9 Permission broker and Rule of Two (TLS-2, BLD-4)

| Tool | Default | Grant | Flags it sets |
| --- | --- | --- | --- |
| List, read, search workspace | On | None needed | None by itself |
| Write, append, patch workspace | On | None needed; size limits apply | None |
| Read imported documents | On when attached | None needed | A |
| Web fetch and search (P1) | Off | Per-task grant and domain allowlist | A and C |
| Shell and code (P1) | Off | Per-task grant; each command checked against an allowlist | B and C |
| MCP tool (P1) | Off | Per-server and per-tool grant | As declared by the server; unknown means A, B and C |

For every call the broker checks, in order: the tool is in the task's allowlist, the arguments are valid and inside limits, the path stays in the jail, rate and size limits hold, and the user's grant mode allows it (Always, Ask once per run, Ask each time, Never). Every decision writes `tool.called` or `tool.denied`.

Flags are computed by code, never from what the model says. A is set when the task has attached documents, a tool that returns external content, or an input handoff from a task that had A; it is cleared only by an approval gate at which the user reviewed that handoff. B is set when the task can reach data outside the run workspace or marked private, or can run commands. C is set when the task can send data off the machine or change anything outside the workspace.

### 5.10 Rerun from here (RUN-6)

Choosing Rerun from here on task N opens a confirmation that lists tasks N to the last with their results. On confirm: N and all later tasks become stale, then pending; their old attempts and handoffs stay in history; files written by later tasks stay in the workspace and are labelled `from an earlier attempt`; constraints resolved after N are reopened; and the scheduler restarts at N. Restoring the workspace to its state at the end of task N minus one, using recorded file hashes, is P1.

## 6. Data specifications

### 6.1 Plan schema v1 (PLN-1, BLD-6)

A plan is one JSON object with these top-level fields: `schema_version` (`1`), `id`, `title`, `goal`, `created_at`, `options` (granularity, max\_tasks, stage\_barriers), `constraints` (array) and `tasks` (array).

Task fields:

| Field | Type | Required | Rule |
| --- | --- | --- | --- |
| id | string | Yes | V-02 |
| type | enum | Yes | task, collector, gate, input, output or note; default task |
| title | string | Yes | 1 to 80 characters |
| instructions | string | Yes, except gate and note | 20 to 4,000 characters |
| needs | string array | Yes | Earlier task ids, at most 8 |
| expected\_output | string | No | Free text |
| constraints | string array | No | Ids of constraints that apply, at most 12 |
| role | enum | No | planner, worker or collector; default worker, or collector for collector tasks |
| model | object | No | provider\_id and model\_id |
| params | object | No | temperature, top\_p, max\_tokens, seed, stop, context\_length |
| tools | string array | No | Default: list\_files, read\_file, write\_file, search\_files, patch\_file |
| limits | object | No | retries, timeout\_seconds, max\_turns, max\_tool\_calls, max\_tokens |
| gate\_before | boolean | No | When true, an approval gate runs before the task |

Constraint fields:

| Field | Type | Rule |
| --- | --- | --- |
| id | string | c1, c2 and so on; unique |
| text | string | 1 to 1,000 characters, copied from the goal when possible |
| state | enum | active or resolved |
| prerequisite | string or null | What must be true before the restricted action |
| authority | string | user, task:id or tool:name; who may resolve it |
| fallback | string or null | What to do while it is unresolved |
| scope | string array or all | Task ids it applies to |
| origin | enum | goal, planner or user |
| source\_span | array or null | Character range in the goal when copied from it |

### 6.2 Handoff schema (RUN-3)

| Field | Type | Rule |
| --- | --- | --- |
| task\_id | string | Must match the current task |
| status | enum | done, partial or blocked |
| summary | string | At most 400 words |
| key\_facts | array of fact and source | May be empty; every fact needs a source (H-06) |
| binding\_constraints | array | Every constraint in the task's context with id, text, state, prerequisite, authority, fallback and resolution\_evidence (H-03, H-05) |
| decisions | array of decision and basis | Optional |
| open\_issues | array of issue and constraint\_id | A blocking issue names its constraint |
| deliverables | array of path and sha256 | H-07 |
| raw\_output\_ref | string | Written by the engine |
| usage | object | Written by the engine: tokens\_in, tokens\_out, duration\_ms, model, params |

Example of the block a model writes:

```json
{
  "task_id": "t3",
  "status": "done",
  "summary": "Compared the three suppliers on price and lead time.",
  "key_facts": [
    { "fact": "Supplier B is cheapest at 4.10 per unit", "source": "tool:call_17" }
  ],
  "binding_constraints": [
    { "id": "c1", "text": "Never publish figures without a source", "state": "active" }
  ],
  "decisions": [
    { "decision": "Exclude supplier C", "basis": "No lead time published" }
  ],
  "open_issues": [],
  "deliverables": [ { "path": "comparison.md", "sha256": "9f2c..." } ]
}
```

### 6.3 Events (RUN-5, OBS)

Every event has `event_id`, `run_id`, `seq`, `ts` (UTC, ISO 8601), `schema_version`, `prev_hash` and `hash`. The event-specific fields are:

| Event | Payload |
| --- | --- |
| run.created | plan\_version\_id, goal hash, caps |
| run.paused, run.resumed, run.cancelled | reason |
| task.ready | task\_id |
| gate.waiting | task\_id, reason (user or rule\_of\_two), shown\_hash |
| gate.approved, gate.rejected | task\_id, shown\_hash, decision, reason |
| attempt.started | task\_id, attempt\_id, provider, model, params, request\_hash |
| message.added | attempt\_id, role, content reference, tokens |
| tool.called | attempt\_id, tool, arguments hash, result reference, duration |
| tool.denied | attempt\_id, tool, rule |
| compaction.applied | task\_id, ladder step, tokens before and after |
| handoff.recorded | task\_id, attempt\_id, handoff reference, validation result |
| handoff.edited | task\_id, revision, reason |
| constraint.released | constraint\_id, authority, evidence reference |
| task.succeeded, task.failed, task.skipped, task.stale | task\_id, code |
| run.succeeded, run.failed | totals |

### 6.4 Storage layout

The data root defaults to the platform's per-user application data folder, in a folder named `LocalAgentStudio`, and the user can move it in Settings. Inside it:

- `studio.db`: the SQLite database. The `events` table is the source of truth for runs. The tables `providers`, `plans`, `plan_versions`, `templates`, `role_profiles` and `settings` hold user data. The tables `tasks`, `constraints`, `runs`, `attempts`, `handoffs`, `artifacts` and `models_cache` are derived from events and can be rebuilt.
- `plans/` and `templates/`: JSON exports kept beside the database for easy backup.
- `runs/<run_id>/workspace/`: the agents' file area. `runs/<run_id>/attempts/<task_id>/<attempt_id>/` holds `request.json`, `stream.log` and `final.txt`. `runs/<run_id>/handoffs/<task_id>.json` holds validated handoffs. `runs/<run_id>/export/` holds exports.
- `logs/` (redacted, rotated at 10 MB, five files) and `backups/` (database copies made before each migration).

Secrets live in the OS keychain under the name `LocalAgentStudio/provider/<provider_id>`. Where no keychain exists (for example a headless Linux session) the app refuses to store a key and offers `env:NAME` references to environment variables instead. Migrations are numbered; the app backs up the database before each one and refuses to open a database written by a newer version.

### 6.5 Selected settings keys

| Key | Default |
| --- | --- |
| ui.theme | system |
| ui.reduce\_motion | follow system |
| ui.text\_scale | 100 percent |
| runs.failure\_policy | ask |
| runs.cap\_tokens | 2,000,000 |
| runs.cap\_wall\_minutes | 480 |
| data.retention | keep forever |
| export.ai\_notice | true |
| export.redact\_prompts | false |
| updates.channel | stable |
| updates.auto | false |
| privacy.crash\_reports | false |
| engine.port | a random free loopback port |

## 7. Interface specifications

### 7.1 Provider adapter contract (PRV-3, PRV-4)

Every adapter implements: `probe()` returning a capability record; `list_models()`; `chat(request)` returning a stream of events (text delta, reasoning delta, tool call, usage, done); `cancel(stream_id)`; and, where the server supports them, `count_tokens(messages, model)`, `preload(model)` and `unload(model)`.

The internal request holds the model, ordered messages (system, user, assistant, tool result), tools, an optional JSON schema, parameters and `stream: true`. The Chat Completions adapter maps it to the chat completions endpoint. The Responses adapter maps it to the responses endpoint with the full history in the input and never sends a previous response id. Adapter errors are normalized to `AUTH`, `UNREACHABLE`, `TIMEOUT`, `MODEL_NOT_FOUND`, `CONTEXT_EXCEEDED`, `RATE_LIMIT`, `SERVER_ERROR` and `BAD_REQUEST`.

### 7.2 Provider presets

The defaults and endpoints below come from vendor documentation read in November 2026. Phase 0 verifies each against a live server.

| Preset | Default address | Authentication | Notes |
| --- | --- | --- | --- |
| LM Studio | `http://localhost:1234` | Bearer token when API tokens are enabled | OpenAI-style endpoints for inference; the native v1 API for model load and unload (P1); JSON schema through grammar sampling |
| Unsloth Studio (Beta) | Shown by the server; the port varies, often 8000 or 8888 | Bearer key starting `sk-unsloth` | Models list has a loaded flag; token counting at `/v1/chat/count_tokens`; always send `enable_tools: false`; Responses history goes in the input |
| Ollama | `http://localhost:11434` | None by default | OpenAI-compatible endpoints under `/v1` |
| llama.cpp server | `http://localhost:8080` | Optional API key | Router mode adds `/models`, `/models/load` and `/models/unload` |
| vLLM | `http://localhost:8000` | Optional key | Structured-output support depends on the server version |
| Generic OpenAI-compatible | Entered by the user | Optional | The capability probe decides what is used |

### 7.3 Capability probe

1. Request the model list: reachable, authenticated, models available.
2. For the selected model, send a 5-token streamed chat: streaming and time to first token.
3. Ask the model to call a trivial `ping` tool: tool calling passes if a valid call comes back.
4. Request a tiny JSON schema: structured output passes if the reply validates.
5. Call the token-count endpoint when one exists; otherwise the result is Unknown.
6. Read context length from server metadata; if absent, mark Unknown and offer a manual field.
7. For llama.cpp, request `/models` to detect router mode.

Results are cached by provider, model and server version for 24 hours and refreshed when the version changes. A probe makes at most six small requests and uses at most 2,000 tokens in total.

### 7.4 CLI (API-1)

| Command | Purpose |
| --- | --- |
| `studio plan "GOAL" -o plan.json` | Generate a plan; options `--granularity` (coarse, normal or fine), `--max-tasks N`, `--provider NAME`, `--model ID` |
| `studio run PLAN_OR_TEMPLATE` | Run a plan; options `--goal TEXT`, `--var key=value` (repeatable), `--provider NAME`, `--planner-model ID`, `--worker-model ID`, `--json` |
| `studio list` | List runs; options `--status S`, `--json` |
| `studio inspect RUN_ID` | Show a run; options `--task ID`, `--json` |
| `studio resume RUN_ID` | Resume a paused or interrupted run |
| `studio approve RUN_ID GATE_ID` | Approve a gate; `--reason TEXT` is required; `studio reject` takes the same arguments |
| `studio export RUN_ID` | Export; options `--format` (markdown or json), `-o PATH` |
| `studio providers test [NAME]` | Run the capability probe |

Output is human-readable by default. With `--json` each command prints one JSON object, and `run` prints events as JSON lines. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Run succeeded |
| 1 | Run failed |
| 2 | Invalid plan or arguments |
| 3 | Provider unreachable or authentication failed |
| 4 | Waiting at a gate in non-interactive mode |
| 5 | A cap was reached |
| 6 | Run cancelled |
| 7 | Internal error |

### 7.5 Engine API (internal in 1.0, public in P1)

The engine listens on `127.0.0.1` only. Every request carries `X-Studio-Token`, a random token created at launch and passed to the interface over IPC; the CLI reads it from a per-user socket file with mode 0600. Requests with an unexpected Origin or Host header are refused with 403.

| Method and path | Purpose |
| --- | --- |
| GET /health | Engine status and version |
| GET, POST /providers; PUT, DELETE /providers/{id} | Manage profiles |
| POST /providers/{id}/probe; GET /providers/{id}/models | Capability probe and model list |
| POST /plans; GET, PUT /plans/{id} | Generate, read and save plans |
| POST /runs; GET /runs; GET /runs/{id} | Start and read runs |
| GET /runs/{id}/events?after=seq | Event stream from a sequence number |
| POST /runs/{id}/pause, /resume, /cancel | Run controls |
| POST /runs/{id}/tasks/{task\_id}/retry, /skip, /rerun | Task controls |
| POST /runs/{id}/gates/{gate\_id}/approve, /reject | Gate decisions |
| PUT /runs/{id}/handoffs/{task\_id} | Edit a handoff with a reason |
| GET /runs/{id}/export?format= | Export |
| GET, POST /templates | Templates |

Errors return `{ "error": { "code", "message", "fix" } }` with status 400, 401, 403, 404, 409, 422 or 500.

## 8. Message catalogue

Every error has a plain message and a fix. Messages are at most two sentences, name the thing that failed, and never blame the user. Placeholders are in braces.

| Code | Message | Fix shown |
| --- | --- | --- |
| AUTH | `The server rejected the key.` | `Create a new key in the server's settings and replace it under Providers.` |
| UNREACHABLE | `Cannot reach the server at {url}.` | `Start the server and its local API, then press Test connection.` |
| MODEL\_NOT\_FOUND | `The model {id} is not available on this server.` | `Load it in the server, or choose another model.` |
| RUN-CONTEXT-OVERFLOW | `This task's input is larger than the model's context.` | `Split the task, or choose a model with a longer context.` |
| HANDOFF-INVALID | `The result did not follow the required format: {rules}.` | `Retry, edit the task instructions, or use a stronger model.` |
| TOOL-CALL-INVALID | `The model made a tool call that could not be read.` | `Retry, or turn tools off for this task.` |
| LOOP-DETECTED | `The agent repeated itself and was stopped.` | `Retry with clearer instructions or another model.` |
| CAP-REACHED | `The run reached its {cap} cap.` | `Raise the cap in Settings > Runs, then resume.` |
| PERMISSION-DENIED | `The task tried to use {tool}, which it is not allowed to use.` | `Grant the tool in the task's Tools tab if you trust this task.` |
| PLAN-INVALID | `The plan has {n} problems.` | `Open the Lint tab and fix the errors.` |
| GATE-REQUIRED | `This task needs your approval before it runs.` | `Review it and approve it in the run view.` |
| DB-NEWER | `This data was written by a newer version of the app.` | `Update the app. Your data was not changed.` |
| UNSIGNED-UPDATE | `The update could not be verified and was not installed.` | `Download the installer again from the official page.` |
| DOWNGRADE-BLOCKED | `The update is older than the installed version and was refused.` | `No action is needed.` |

## 9. Accessibility and keyboard specification (COM-5)

The standard is WCAG 2.2 AA in both themes and in high-contrast mode.

### Keyboard

| Action | Windows and Linux | macOS |
| --- | --- | --- |
| Command palette | `Ctrl+K` | `Cmd+K` |
| New goal | `Ctrl+N` | `Cmd+N` |
| Run | `Ctrl+Enter` | `Cmd+Enter` |
| Pause after this task | `Ctrl+.` | `Cmd+.` |
| Undo and redo | `Ctrl+Z`, `Ctrl+Shift+Z` | `Cmd+Z`, `Cmd+Shift+Z` |
| Move selected task earlier or later | `Alt+Up`, `Alt+Down` | `Option+Up`, `Option+Down` |
| Open the inspector for the selected task | `Enter` | `Return` |
| Switch between Canvas and List | `Ctrl+Shift+L` | `Cmd+Shift+L` |
| Move to the next region | `F6` | `F6` |
| Show shortcuts | `Ctrl+/` | `Cmd+/` |

All shortcuts can be changed in Settings. No shortcut is the only way to do something.

### Focus and structure

- Focus order: navigation, top bar, toolbar, canvas or list, inspector, bottom dock. `F6` cycles between regions.
- The focus indicator is at least 2 px with 3:1 contrast. Dialogs trap focus and return it to the control that opened them.
- Interactive targets are at least 24 by 24 CSS pixels.
- Layout reflows at 400 percent zoom without two-dimensional scrolling, except inside the canvas. Text scale in Settings runs from 100 to 200 percent.

### Screen readers

- Each canvas card is a button with a full name, for example `Task 3 of 8: Compare suppliers, succeeded, uses t1 and t2, risk flags 1 of 3`.
- Status changes use a polite live region (`Task 3 succeeded`). Failures and gates use an assertive one. Streaming text is announced once (`Task 3 is writing`), not token by token.
- Settings has a `Use List view by default` option, because the list is the primary accessible editor.
- Errors are tied to their fields and summarized at the top of the dialog.

### Visual and motion

- Meaning never rests on colour alone: every status has an icon and text.
- Text contrast is at least 4.5:1 and interface components at least 3:1.
- The pulse on a running task stops when reduce motion is on. Nothing flashes more than three times a second.

## 10. Acceptance scenarios

| ID | Scenario | Pass condition |
| --- | --- | --- |
| AC-01 | First run (DST-2): a machine with LM Studio running and no profile | The wizard creates a profile, models are chosen and the self-test passes, in under 10 minutes |
| AC-02 | Plan repair (PLN-2): the planner returns a dependency on a later task | Up to three repair rounds run; an unrepaired plan opens in S4 with V-03 shown |
| AC-03 | Constraint extraction (PLN-4): the goal says `never contact customers` | The plan lists that text verbatim as a constraint, highlighted in the goal |
| AC-04 | Isolation (RUN-1): a canary phrase is planted in task 1's output | Task 3, which does not need task 1, receives a request with no canary |
| AC-05 | Constraint preservation (RUN-3): a model rewrites a constraint in its handoff | H-03 fails, one repair is requested, and a second failure ends the attempt with `HANDOFF-INVALID` |
| AC-06 | Resume (RUN-5): the engine is killed during task 4 | After restart tasks 1 to 3 are not rerun, task 4 restarts as a new attempt, and the event hash chain verifies |
| AC-07 | Caps (RUN-8): a token cap of 10,000 | The run stops with `CAP-REACHED` after at most one model call beyond the cap |
| AC-08 | Rule of Two (BLD-4): a task has attached documents, a shell tool and a network tool, with no gate | Run is blocked with L-01; adding a gate clears it |
| AC-09 | Provider tools (PRV-5): a mock Unsloth server | Every request carries `enable_tools: false` |
| AC-10 | Context overflow (RUN-9): handoffs exceed the available input | The ladder runs in order, never changes constraints, logs `compaction.applied`, and stops with `RUN-CONTEXT-OVERFLOW` only if it still does not fit |
| AC-11 | Keyboard (BLD-1): keyboard only | The user can reorder a task, add a dependency and delete a task, including the warning about dependents |
| AC-12 | Privacy (SET-3): default settings, a full run | A network capture shows connections only to the configured server |
| AC-13 | Rerun (RUN-6): rerun from task 3 | Tasks 3 to the last become stale, old attempts stay in history, constraints resolved after task 3 reopen |
| AC-14 | Updater (DST-1): a manifest with an older version, or with a bad signature | It is refused with `DOWNGRADE-BLOCKED` or `UNSIGNED-UPDATE` and the installed version is unchanged |

## 11. Traceability to the PRD

| PRD requirements | Specified in |
| --- | --- |
| PRV-1 to PRV-7 | S1, S9, sections 7.1 to 7.3 |
| PLN-1 to PLN-5 | S3, sections 5.1, 5.2 and 6.1 |
| PLN-6, PLN-7 (P1) | S3 attachments, S4 Plan menu |
| BLD-1 to BLD-8 | S4, section 5.3 |
| RUN-1 to RUN-9 | Sections 5.4 to 5.8 and 5.10, S6, S8 |
| TLS-1 to TLS-4 | Section 5.9, S4 Tools tab |
| TLS-5 to TLS-8 (P1) | Section 5.9 rows marked P1 |
| HIL-1 to HIL-3 | S7, section 5.5 (H-05) |
| OBS-1 to OBS-4, OBS-7 | S2, S6, S12, S13, section 6.3 |
| LIB-1, LIB-2 | S10 |
| SET-1 to SET-4 | S11, section 6.5 |
| API-1 | Section 7.4 |
| API-2 (P1) | Section 7.5 |
| DST-1 to DST-3 | S1 and AC-14 here; installer and signing detail is in the PRD's section 19 |
| Non-functional requirements | Sections 5.8, 9 and 10 |
| Security and privacy | Sections 5.9, 6.4 and 7.5 |
| COM-5 | Section 9 |

## 12. Open items

- The exact native LM Studio endpoint paths and request fields for model load and unload, needed for PRV-8, are confirmed in Phase 0.
- Unsloth Studio's default port and its endpoint list can differ by version and backend; confirm against a live server.
- Not every server reports context length; where it is missing, the manual field in S9 is the fallback.
- The 120-second stall timeout, the loop thresholds and the compaction ladder sizes are defaults to tune on the benchmark.
- Visual design (colour, type, icons, final layouts) is produced by design in Phase 0 against this behavior specification. Wireframes are not part of this document.
- RUN-10 (verification session) and RUN-11 (replay), and the detailed behavior of the P1 tools (shell, web, MCP), are specified at the start of the phases that deliver them.
