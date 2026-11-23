# Local Agent Studio: Product Requirements Document

Version 2.0, revised after research · November 20, 2026 · Status: ready for review · Working name: Local Agent Studio

## 1. Summary

Local Agent Studio is a desktop application that turns one large prompt into a chain of small tasks and runs each task as its own isolated agent session, one after another, on models running entirely on the user's machine. A planner agent splits the goal into tasks. The user reviews and edits the plan on a visual canvas. A scheduler then runs every task in a fresh session with its own context, passes results forward through a structured handoff, and lets collector tasks combine earlier results before the next stage starts.

It works offline against local model servers such as LM Studio, Ollama, llama.cpp server and vLLM, and against any other OpenAI-compatible endpoint. This document defines the complete 1.0 product: scope, requirements, quality bars, architecture constraints, release plan and risks. It supersedes the single-file prototype (agent\_studio.py); its gaps are listed in Appendix A.

Version 2.0 adds a standards layer built from research: provider adapters over open specifications (section 6), a structured handoff that stops requirements eroding between tasks (section 15), a security model mapped to the OWASP agentic risks (section 12), and compliance and release-engineering requirements for the EU Cyber Resilience Act and AI Act (sections 13 and 19).

## 2. Problem

- A single long agent session degrades on local models. Context fills up, instructions drift, and small models lose track of earlier steps.
- Existing orchestration tools tend to be code-first libraries, cloud-first platforms, or visual builders that need Docker or a database and assume a fixed, hand-drawn workflow. We have not found one that combines runtime planning, strictly isolated sequential sessions and a local-first, install-and-run experience.
- Users cannot see what each agent was given, why a task failed, or how to fix one step and resume from the middle without starting over.

Research since the first draft sharpens the third point. Handoffs between agents are where multi-agent systems lose requirements that were already established, so the product treats a handoff as a structured, validated record and not as a summary (section 6).

## 3. Goals and non-goals

### Goals

- G1 Reliable decomposition: a planner whose output is validated, repaired automatically when possible, and always editable by the user.
- G2 Strict isolation and order: every task is a new session with its own context; exactly one session is active per run.
- G3 Fully local: no network traffic except to endpoints the user configures; no telemetry by default.
- G4 Control and transparency: every prompt, response and tool call is visible, editable and replayable; any run can resume from any task.
- G5 Modest hardware: usable with small models on 16 GB RAM or less, degrading gracefully rather than failing.
- G6 Production quality: signed installers, automated tests, accessibility, documentation and a support path.

* G7 Standards-based and future-proof: provider, tool and telemetry interfaces follow open specifications behind adapters, so servers and specifications can change without an engine rewrite.
* G8 Compliance-ready: designed from day one for EU Cyber Resilience Act and AI Act duties, WCAG 2.2 AA and a documented support policy.

### Non-goals for 1.0

- Cloud hosting, accounts, or multi-user collaboration.
- Parallel agent execution (the architecture must not prevent it later).
- Sub-agent hierarchies that share one context.
- Downloading, training or fine-tuning models; we integrate with the tools that do.
- Mobile apps and a public template marketplace.

## 4. Users and jobs to be done

| Persona | Context | Needs |
| --- | --- | --- |
| Maker-developer | Runs LM Studio or Ollama, automates multi-step work | Reliable chains (research, draft, review) with no cloud dependency, scriptable from a CLI |
| Privacy-bound professional | Analyst, lawyer or researcher with confidential documents | Offline operation, an audit trail of everything the model saw, no data leaving the machine |
| Local-model tinkerer | Compares models per stage and tunes prompts | Per-task models and parameters, transparent transcripts, repeatable runs |

Core jobs: turn a big request into a reviewed plan; run it unattended with confidence; understand and repair a failure; reuse a good plan for the next request.

## 5. Product principles

1. Local first: nothing requires the internet after installation.
2. Isolation over cleverness: simple fresh sessions beat one long, fragile conversation.
3. Everything inspectable: no hidden prompts, no hidden context.
4. The human can always step in: pause, edit, retry, skip, approve.
5. Small-model friendly: strict schemas, short tasks, budgeted context.
6. Least agency: tools beyond workspace files are off until granted, and every grant is recorded.
7. Constraints travel intact: requirements move between tasks as structured data, never only as a summary.
8. Standards over lock-in: open specifications behind adapters, so no single vendor decides our future.
9. Boring reliability: a durable, deterministic state machine underneath.

## 6. Research findings and design decisions

The research behind this revision covers twelve areas as of November 20, 2026. Each row pairs what the sources say with the decision it drives; section 22 lists the pages.

| Area | What the sources say | Decision |
| --- | --- | --- |
| [LM Studio](https://lmstudio.ai/docs/developer/rest) | Version 0.4.0 added a native v1 REST API with API-token authentication, model download, load and unload endpoints, and streaming events for model loading. It also serves OpenAI-style chat completions and responses. Context length can be set per request only on the native chat endpoint. Structured output uses grammar sampling for GGUF models, and [models under 7B may not support it well](https://www.lmstudio.ai/docs/developer/openai-compat/structured-output). | Use the native API for model lifecycle and token auth, the OpenAI-style endpoints for inference, and keep the planner repair loop for small models. |
| [Unsloth Studio](https://unsloth.ai/docs/basics/api.md) (Beta) | Loaded models are served through llama-server behind API keys that start with sk-unsloth. It exposes chat completions, responses and messages endpoints, a models list with a loaded flag, and a token-count endpoint. The port varies by install, typically 8000 or 8888. Server-side tools (web search, Python, terminal) run on the host, are on by default for unsloth run, and are switched off per request with enable\_tools false or for the whole server with --disable-tools. Responses history must be sent in input; server-managed history is unsupported. | First-class preset that requires a key. Always send enable\_tools false, warn when a server accepts tools, always send full history, and use the token-count endpoint when present. |
| [llama.cpp server](https://dev.to/rosgluk/unload-all-llamacpp-router-models-without-restarting-8dj) | Router mode lists, loads and unloads models one at a time through /models endpoints, evicts the least recently used model at a configured maximum, and reloads an unloaded model when a request names it. There is no documented unload-all call. | Preload and unload per model only, never assume a model stays unloaded, and detect router mode in the capability probe. |
| [Open Responses](https://thenewstack.io/openai-donates-open-responses/) | OpenAI donated this vendor-neutral spec, derived from its Responses API, to the Linux Foundation's Agentic AI Foundation on October 22, 2026. It standardises messages, tool calls and streaming events, and LM Studio, Ollama, vLLM, Hugging Face and OpenRouter are listed as supporters. | One internal message model shaped like Responses items, two wire adapters (Chat Completions and Responses), and the public compliance tests run against each provider. |
| [MCP](https://workos.com/blog/mcp-2026-spec-agent-authentication) | The 2026-07-28 revision removes protocol-level sessions and the connection handshake: version, client info and capabilities travel with every request, and server/discover replaces the up-front exchange. [Authorization is OAuth 2.1 and Dynamic Client Registration is deprecated](https://www.descope.com/blog/post/july-2026-mcp-revision) in favour of Client ID Metadata Documents. | MCP is an optional, isolated adapter: local stdio servers first, target 2026-07-28 with a compatibility mode, never auto-connected, allowlisted per task. |
| [OpenTelemetry GenAI](https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained) | Every gen\_ai attribute is still at Development status. The conventions moved to their own repository on June 12, 2026, which had no tagged release as of mid-July. | The engine's own versioned event schema is canonical. An optional OTLP exporter maps it to a pinned convention version and is off by default. |
| Multi-agent failures ([O'Reilly](https://www.oreilly.com/radar/?p=18124), [arXiv handoff study](https://arxiv.org/pdf/2608.24569)) | An analysis of more than 1,600 traces found roughly 36 percent of multi-agent failures were misalignment between agents. A 2026 study found that summaries and delegation turn binding requirements into optional caveats: ownership deferral deactivated 76.7 percent of blockers and multihop compression 97.2 percent. Restoring a four-field blocker (stop status, prerequisite, authority, fallback) raised recognition from 6.2 to 99.2 percent and cut forbidden actions from 85.2 to 0. Authorization prompts also cut forbidden actions but left the handoff degraded. Separate work reports that [compaction can silently erase safety constraints](https://arxiv.org/pdf/2606.22528). | Handoffs carry binding constraints as structured fields copied verbatim and checked by code. Compaction never touches them. A downstream prompt is never the only defence against what a handoff lost. |
| [Rule of Two](https://simonwillison.net/2025/Nov/2/new-prompt-injection-papers/) | Meta's framework says that within one session an agent should have at most two of three properties: it processes untrusted input, it reaches sensitive systems or private data, it changes state or communicates externally. | Every task gets a computed capability profile. A task with all three needs an approval gate, or the run is blocked. |
| OWASP agentic risks ([OWASP](https://genai.owasp.org/resource/owasp-top-10-for-agentic-application), [summary](https://www.techrepublic.com/article/news-owasp-ai-agent-risk-list/)) | The Top 10 for Agentic Applications 2026 lists ten risks, from ASI01 goal hijack to ASI10 rogue agents, and recommends least agency: grant only the autonomy a bounded task needs. | Section 12 maps each risk to a control, and every default grants the least agency. |
| [EU Cyber Resilience Act](https://www.orrick.com/en/Insights/2026/09/EU-Cyber-Resilience-Act-New-Guidance-Clarifies-Scope-Reporting) | Manufacturers' duties to report actively exploited vulnerabilities and severe incidents have applied since September 11, 2026; most other obligations apply from December 11, 2027. Non-commercial open source is outside scope, but [commercial distribution can make the publisher a manufacturer](https://www.goodwinlaw.com/en/insights/publications/2026/09/alerts-lifesciences-technology-preparing-for-eu-cyber-resilience-act), generally with at least five years of support. | Build vulnerability handling, SBOMs, signed releases and a support policy in from 1.0, whichever licence is chosen (OQ-4). |
| [EU AI Act](https://natlawreview.com/article/eu-digital-omnibus-ai-enters-force) | The Digital Omnibus on AI is in force. Article 50 transparency duties apply from August 2, 2026, with [provider marking of generated content postponed to December 2, 2026](https://www.hoganlovells.com/en/publications/eu-legislators-agree-to-delay-for-highrisk-ai-rules). Standalone high-risk obligations move to December 2, 2027. Reported guidance says a system [presented as broadly applicable, without excluding high-risk uses, may be treated as high-risk](https://www.mondaq.com/intellectual-property/1825624/eu-ai-act-news-digital-omnibus-on-ai-new-guidance-on-risk-classification-gpai-and-transparency-obligations). | The product ships no models. Exports carry provenance and an AI-generated notice option, the intended-use statement excludes high-risk uses, and counsel reviews it before EU distribution. |
| Release engineering ([code signing](https://www.ssl.com/article/reminder-reduced-code-signing-certificate-lifecycles/amp/), [Artifact Signing](https://techcommunity.microsoft.com/blog/microsoft-security-blog/-/4482789), [CVE-2026-95625](https://osv.dev/vulnerability/CVE-2026-95625)) | Public code-signing certificates have been limited to about 458 days since March 1, 2026, and Microsoft's Artifact Signing is generally available. An advisory showed that an updater that signs only the binary, not its manifest, can be forced to install an older signed release; the fix is in tauri-plugin-updater 2.12.0. | Sign every artifact in CI with timestamps and a managed key service. Publish our own signed update manifest and reject downgrades, whichever shell we use. |

## 7. Core concepts

- Goal: the user's original prompt, plus optional attached files.
- Plan: an ordered, versioned set of tasks produced by the planner and edited by the user.
- Task: one unit of work with instructions, dependencies, model, tools and limits.
- Collector: a task whose purpose is to combine several earlier results.
- Binding constraint: a requirement that restricts what later tasks may do until it is explicitly resolved, recorded with a stop status, prerequisite, authority and fallback.
- Session: a fresh conversation created for one task attempt. Sessions never share history.
- Handoff: the structured result a task passes forward: status, summary, key facts, binding constraints and files.
- Run: one execution of a plan, with durable state and a full event log.
- Workspace: a per-run folder where agents read and write files.
- Provider: a configured model server. Model role: planner, worker or collector.
- Capability profile: the three Rule of Two flags computed for a task from its tools and sources.
- Gate: a point where the run pauses for human approval.

## 8. Key user journeys

1. First run: install, the app detects a running local server, the user picks models, a sample goal runs end to end in under ten minutes.
2. Plan, edit, run, inspect: enter a goal, review the generated tasks on the canvas, fix one instruction, run, open any task to read its transcript, export a report.
3. Failure and recovery: task 6 fails on a malformed answer; the user opens its transcript, edits the instructions, and reruns from task 6 without redoing tasks 1 to 5.
4. Reuse and automate: save the plan as a template with variables, then run it from the CLI with a new topic.

## 9. Functional requirements: core

P0 means required for 1.0 general availability, P1 means within two releases after 1.0, P2 means later. Every line ends with the check that proves it is done.

### 9.1 Providers and models (PRV)

- PRV-1 (P0) Connection profiles: name, base URL, API key held in the OS keychain, extra headers, timeouts. Presets for LM Studio (default http://localhost:1234), Unsloth Studio, Ollama, llama.cpp server, vLLM and any generic OpenAI-compatible server. Done when each preset connects, lists models and completes a chat against a real server in the nightly suite.
- PRV-2 (P0) Authentication for servers that require a token (LM Studio API tokens, Unsloth keys). A 401 names the exact fix. Done when a missing or revoked key leads to the fix in two clicks.
- PRV-3 (P0) Two wire adapters over one internal message model: Chat Completions and Responses (Open Responses compatible). The engine is stateless toward the server: it always sends full history and never relies on server-managed conversation state. Done when the same plan runs unchanged through both adapters on LM Studio.
- PRV-4 (P0) Capability probe cached per model and server version: streaming, tool calling, JSON-schema output, context length, reasoning output, token counting, router mode. Fallbacks: prompt-based JSON, a text tool protocol, a local token estimate. Done when removing any one capability degrades a run without failing it.
- PRV-5 (P0) Server-side tool policy. The engine never relies on provider-side tools. For Unsloth it sends enable\_tools false on every request and flags servers that accept tools. Done when a test server that executes tools never receives an enabled request.
- PRV-6 (P0) Model roles (planner, worker, collector) with per-task overrides and per-role parameters: temperature, top\_p, max tokens, seed, stop sequences, context length. Done when overrides appear in the context preview and the run record.
- PRV-7 (P0) Streaming with cancel. Reasoning blocks are stored, kept out of handoffs and shown on demand. Done when cancel stops generation within 2 seconds on every preset.
- PRV-8 (P1) Model lifecycle control where the server allows it: preload before a task, order tasks to minimise swaps, unload after the run (LM Studio load and unload endpoints, llama.cpp router endpoints). Done when cold-start timeouts disappear from the benchmark.
- PRV-9 (P1) Embeddings for workspace retrieval.

### 9.2 Planner (PLN)

- PLN-1 (P0) Schema-validated tasks: id, title, instructions, needs, expected output, binding constraints, suggested role and tools, size estimate. Done when every stored plan validates against schema v1.
- PLN-2 (P0) Validation and repair: unique ids, no cycles, dependencies on earlier tasks only, size limits, a final task. Up to three repair rounds, then the partial plan opens in the editor with errors marked. Done when an invalid plan can never reach the scheduler.
- PLN-3 (P0) Constrained decoding with a JSON schema when the model supports it, prompt-based JSON otherwise. Done when the benchmark shows fewer repair rounds with constrained decoding.
- PLN-4 (P0) Constraint extraction: the planner lists the goal's hard requirements (must, never, only, needs approval) as binding constraints with ids, and the user reviews them before the plan is accepted. Done when each constraint is visible on the canvas and in every downstream context preview.
- PLN-5 (P0) Planning options: granularity (coarse, normal, fine), maximum task count, stage barriers, optional critique pass.
- PLN-6 (P1) Re-planning after a task or failure: the planner proposes changes to remaining tasks and the user approves a diff. User-edited tasks stay pinned when a plan is regenerated.
- PLN-7 (P1) Attach files or folders to the goal. The planner sees a manifest and summaries, never whole files.

### 9.3 Visual builder (BLD)

- BLD-1 (P0) A canvas that shows tasks in execution order. Sequence and data dependencies are drawn differently. Users drag to reorder and link; invalid links are rejected with a reason. Done when every drag action has a keyboard and list-view equivalent (WCAG 2.2 dragging alternative).
- BLD-2 (P0) Node types: Task, Collector, Approval gate, Input, Output, Note.
- BLD-3 (P0) Inspector: instructions with variables, model, tools, retries, timeout, token budget, expected output, binding constraints, and a context preview that shows exactly what the agent will receive, counted in tokens.
- BLD-4 (P0) A capability badge on every task showing which Rule of Two properties it has (untrusted input, sensitive access, external effect). Three of three requires an approval gate. Done when the linter blocks a run that has all three without a gate.
- BLD-5 (P0) Live overlay: status, elapsed time, tokens, and click-through to the transcript.
- BLD-6 (P0) Undo and redo, copy and paste, multi-select, auto-layout, zoom, minimap, autosave, version history, versioned JSON import and export.
- BLD-7 (P0) Plan linting: missing dependencies, oversized tasks, unused results, predicted context overflow, constraints that no task carries.
- BLD-8 (P1) Stages (group and collapse), template variables such as {{topic}}, reusable prompt snippets.

### 9.4 Agent runtime (RUN)

- RUN-1 (P0) Session isolation. A task's context holds only the role prompt, the goal, the task instructions, the binding constraints, the handoffs listed in needs and a workspace manifest. Done when a canary phrase planted in task 1 never appears in task 3's context unless a dependency passes it.
- RUN-2 (P0) Strictly sequential scheduling: one active session per run, in plan order. Queued runs execute one at a time by default.
- RUN-3 (P0) Handoff contract (section 15): a structured result with binding constraints carried verbatim. Done when code validation rejects any handoff that drops or rewords a constraint.
- RUN-4 (P0) Collectors accept several handoffs within a token budget. Over budget, a logged compaction runs and never touches pinned fields.
- RUN-5 (P0) Durable, event-sourced state: every transition and message is written before the next action. Crash or power loss resumes from the last completed task. Done when at least 99 percent of 1,000 random kills resume correctly.
- RUN-6 (P0) Controls: start, pause, cancel, retry, skip, rerun from here (downstream results marked stale), edit pending tasks.
- RUN-7 (P0) Failure handling: retries with backoff, timeouts, repair of malformed output, loop and repetition detection, maximum turns, and a failure policy of stop, skip or ask.
- RUN-8 (P0) Hard budgets by default: per-task and per-run caps on tokens, wall time, turns and tool calls. Exceeding a cap stops the run with a clear message. Done when no run overshoots a cap by more than one model call.
- RUN-9 (P0) Context budgeting: detect context length, count tokens (the server's count endpoint when available), warn at 80 percent, compact or refuse before overflow.
- RUN-10 (P1) An optional verification session checks a task's output against its acceptance criteria.
- RUN-11 (P1) Reproducibility: store model id, parameters, seed, prompts and tool results, with a replay mode.
- RUN-12 (P2) Opt-in parallel branches for independent tasks, off by default.

## 10. Functional requirements: platform

### 10.1 Tools and workspace (TLS)

- TLS-1 (P0) A per-run workspace with list, read, write, append, search and patch tools, jailed to the folder with symlinks resolved and size limits. Done when a path-traversal suite passes.
- TLS-2 (P0) A permission broker: only workspace file tools are on by default, with per-task allowlists, ask once, always or never, and an audit entry for every call. The Rule of Two capability profile is computed from the granted tools.
- TLS-3 (P0) Native function calling when supported, otherwise a strict text protocol. A malformed call is repaired once, then fails the attempt.
- TLS-4 (P0) Trust labels: text that comes from files, the web or tool output is marked untrusted in the context and in the transcript.
- TLS-5 (P1) Code and shell execution in an OS-level sandbox with a command allowlist, timeouts, resource limits and no network by default.
- TLS-6 (P1) An MCP client targeting the 2026-07-28 specification, stdio servers first, per-task allowlists, never connected automatically.
- TLS-7 (P1) Web fetch and search, off by default, with a domain allowlist; results are untrusted.
- TLS-8 (P1) Import of PDF, DOCX, Markdown, TXT and CSV with text extraction.

### 10.2 Human in the loop (HIL)

- HIL-1 (P0) Approval gates pause before a task, show its context preview and the previous output, and let the user approve, edit or reject.
- HIL-2 (P0) A handoff can be edited before downstream tasks consume it. The edit is stored as a revision with a reason.
- HIL-3 (P0) A binding constraint is released only by an explicit record of who or what resolved it and the evidence. Until then downstream tasks must treat it as active.
- HIL-4 (P1) Desktop notifications when a run needs attention, and notes on tasks and runs.

### 10.3 History and observability (OBS)

- OBS-1 (P0) Run history with search, filters, status, duration, models and token totals.
- OBS-2 (P0) A transcript viewer per task: system, user, assistant and tool events, with a raw JSON toggle.
- OBS-3 (P0) A timeline with tokens per second, time to first token and tokens per task.
- OBS-4 (P0) Export as a Markdown report or a re-importable JSON bundle. Exports carry provenance: run id, models used, and an optional AI-generated notice.
- OBS-5 (P1) Optional OTLP export, mapped to a pinned GenAI convention version, off by default and local endpoints only unless configured.
- OBS-6 (P1) Compare two runs: plan diff and output diff.
- OBS-7 (P0) A redacted diagnostics bundle for bug reports, reviewed by the user before saving.

### 10.4 Library (LIB)

- LIB-1 (P0) Built-in templates (research report, document analysis, code review, content pipeline) and save-as-template with variables.
- LIB-2 (P0) Role profiles: reusable system prompt, parameters and tool allowlist.
- LIB-3 (P1) Prompt version history, and the model's licence shown in the picker when the server reports it.

### 10.5 Settings and data (SET)

- SET-1 (P0) Light, dark and system themes, customizable shortcuts, English UI with externalized strings.
- SET-2 (P0) Data location, retention rules, delete one run or all data, remove stored keys.
- SET-3 (P0) Telemetry off and no outbound connection except configured endpoints. Opt-in crash reports are P1.
- SET-4 (P0) Manual update check and opt-in automatic update from a signed manifest; offline installers.

### 10.6 CLI and API (API)

- API-1 (P0) CLI to run a plan, list, inspect, resume and export, with JSON output and meaningful exit codes.
- API-2 (P1) A local HTTP API with token authentication and an OpenAPI description, plus local hooks on run completion.

### 10.7 Packaging and onboarding (DST)

- DST-1 (P0) Installers for Windows, macOS and Linux, signed and notarized. No Docker, no external database, no administrator rights for a per-user install.
- DST-2 (P0) A first-run wizard that detects running servers on well-known ports, asks for a key when one is needed, and runs a sample plan as a self-test.
- DST-3 (P0) Documentation: quickstart, user guide, troubleshooting, plan schema reference and a security guide.
- DST-4 (P0) A published release-channel and support policy (section 19).

## 11. Non-functional requirements

Every target below is tested automatically or audited before 1.0 ships.

| Area | Requirement | How it is checked |
| --- | --- | --- |
| Responsiveness | Canvas interactions under 100 ms for plans up to 200 tasks. Cold start under 3 seconds on the reference laptop. Streaming overhead under 50 ms. App memory under 500 MB excluding models. | Performance suite on the reference machines; regressions block release |
| Reliability | No data loss on crash or power loss. At least 99 percent of 1,000 random kills resume correctly. Reconnect and retry when the model server drops. | Fault-injection harness in CI |
| Scale | Runs of 500 tasks, transcripts of several hundred MB with paging, 10,000 runs in history. | Synthetic load tests |
| Cost control | Default hard caps on tokens, wall time, turns and tool calls per task and per run; no run overshoots a cap by more than one model call. | Cap tests with a mock server |
| Hardware | Usable on 16 GB RAM with small models and on CPU only, with progress estimates based on observed tokens per second. Minimum and reference hardware are set in Phase 0 (OQ-5). | Benchmark on the low-end reference machine |
| Platforms | Windows 11, current and previous two macOS releases, Ubuntu 22.04 and later with AppImage, deb and rpm. Windows 10 only if usage data justifies it. | Platform matrix in CI and beta |
| Accessibility | WCAG 2.2 AA, including a single-pointer or keyboard alternative to every drag, full keyboard use of the canvas with a list view alternative, screen-reader labels, reduced motion, contrast in both themes. | Automated checks each build; manual screen-reader pass and external audit before GA |
| Internationalization | Unicode end to end so prompts and outputs in any script work; strings externalized; layout ready for right-to-left languages. | Multilingual test corpus including non-Latin scripts |
| Privacy | No outbound connection except endpoints the user configured. | Automated network-capture test on every release candidate |
| Compatibility | Provider adapters pass the Open Responses compliance tests where a server claims support, and a recorded-conversation suite for each preset. | Nightly contract tests against real servers |
| Maintainability | Semantic versioning, forward-compatible data migrations with automatic backup before migrating, documented plan and event schemas. | Migration tests from every released version |
| Supportability | A diagnostics bundle and a version and provider report that a user can attach to a bug without leaking prompts or keys. | Redaction tests |

## 12. Security, privacy and safety

The product's security rule is least agency: every task starts with the smallest set of tools and permissions it needs, and anything more is an explicit, recorded grant. Prompt injection is treated as unsolved, so the design limits what a hijacked task can do instead of trying to detect every attack.

### Threat model against the OWASP agentic risks

| Risk | How it appears here | Control |
| --- | --- | --- |
| ASI01 Agent goal hijack | A file, web page or tool result tells a task to do something else | Untrusted-content labels, binding constraints in every context, approval gates, Rule of Two linting |
| ASI02 Tool misuse | A task uses a legitimate tool too broadly | Per-task allowlists, argument validation, audit log, size and rate limits |
| ASI03 Identity and privilege abuse | Keys or tokens leak into prompts or tools | Keys in the OS keychain, never in prompts, plans or logs; tools never inherit credentials |
| ASI04 Agentic supply chain | A template, plugin, MCP server or model is malicious | Imported plans never auto-run and list the tools they request; MCP servers allowlisted; signed releases, SBOM, pinned dependencies |
| ASI05 Unexpected code execution | A model writes and runs unsafe code | Code execution off by default and sandboxed when on; provider-side tools blocked (PRV-5) |
| ASI06 Memory and context poisoning | Bad content persists and shapes later runs | No cross-run memory in 1.0; one workspace per run; retrieved text marked untrusted |
| ASI07 Insecure inter-agent communication | A handoff is forged or altered | Handoffs live in a hash-chained local event log and are validated by code before use |
| ASI08 Cascading failures | One bad result spreads down the chain | Sequential isolation, validation at every handoff, budget caps, stop-on-failure by default |
| ASI09 Human-agent trust exploitation | A confident summary talks a user into approving | Gates show the raw context and a diff, not only a summary, and record what was approved |
| ASI10 Rogue agents | A task loops, runs away or acts outside its role | Loop detection, hard budgets, a run-level kill switch, a full audit trail |

### Rule of Two enforcement

The plan linter computes three flags for every task: it reads untrusted input, it can reach sensitive data or systems, and it can change state or communicate externally. A task with all three flags cannot run unless an approval gate precedes it. The flags come from the tools granted and the sources attached, not from what the model says it will do.

### Local engine and data protection

- The engine listens on loopback or an IPC channel only. Every request needs a per-launch token and a checked origin, which blocks other web pages and DNS-rebinding attempts.
- Provider keys are stored in the OS keychain. Logs, exports and diagnostics are redacted of keys and, by default, of prompt text.
- Workspace paths are resolved and jailed; symlinks cannot escape.
- Servers that offer their own tools (Unsloth, LM Studio MCP) are used for inference only; their tools are not enabled by the app.
- The event log is hash-chained, and the engine verifies it on load and refuses a tampered run.

### Software supply chain and updates

- Pinned dependencies, automated dependency review, and a software bill of materials in a standard format published with every release.
- Every installer and update is signed in CI with timestamps. The updater uses our own signed manifest that includes the version, URL and hash, and rejects any older version than the one installed.
- A published vulnerability policy: a security contact, a coordinated disclosure process, acknowledgement within 3 business days, and fix targets by severity.
- A third-party penetration test of the engine, tool runtime and updater before GA.

## 13. Compliance and standards

This section turns the legal and standards research into build requirements. It is not legal advice; counsel reviews it in Phase 4 before any EU distribution.

| ID | Requirement | Priority | Done when |
| --- | --- | --- | --- |
| COM-1 | Cyber Resilience Act readiness: a vulnerability-handling process, a coordinated disclosure policy, a software bill of materials per release, secure-by-default settings, and a documented path for reporting actively exploited vulnerabilities and severe incidents through the EU reporting platform. | P0 by design, mandatory if the publisher counts as a manufacturer (OQ-4, OQ-8) | A tabletop exercise reports a simulated exploited vulnerability inside the required timeline |
| COM-2 | Security-update support period of at least five years after each release if commercially distributed in the EU, stated in the support policy. | P0 | Policy published before GA |
| COM-3 | AI Act positioning: the product ships no models; its intended-use statement excludes high-risk uses such as employment, education, credit and law enforcement; marketing and documentation follow it. | P0 | Counsel sign-off on the statement |
| COM-4 | AI Act transparency support: exports carry provenance (run id, models used) and an optional AI-generated notice; a setting keeps it on by default for exported documents. | P0 | Export tests include the notice and provenance |
| COM-5 | Accessibility: WCAG 2.2 AA conformance and a published accessibility conformance report at GA. | P0 | External audit finds no critical failures |
| COM-6 | Licensing: dependency licence scan in CI, a NOTICE file in every release, and a note that each model carries its own licence, which the user must follow. | P0 | CI blocks an incompatible licence |
| COM-7 | Privacy: no data collection by default, a data map in the documentation, one-step deletion of all data, and a privacy notice for any opt-in crash reporting. | P0 | Network-capture test shows zero outbound connections by default |
| COM-8 | Regulatory watch: a named owner reviews CRA, AI Act and accessibility rule changes every quarter and records the outcome. | P1 | Review log kept |

## 14. Architecture

The architecture is proposed here and confirmed by decision records in Phase 0. Everything runs on the user's machine, and the model servers are the only external dependency.

&#91;embedded content: architecture · 3 layers, 6 engine components\]

The shell and the CLI talk to one engine over loopback with a per-launch token. The scheduler is the only component that starts agent sessions, and the provider adapters are the only code that talks to model servers.

- Shell: Tauri is preferred for its size and capability-based permission model. Electron is the fallback if canvas performance or accessibility in the system WebView falls short (OQ-3). Either way the UI holds no secrets and has no file access of its own.
- Engine: a separate sidecar process that the shell starts and stops. Its language is decided in Phase 0 (OQ-2). It exposes commands over REST and events over a streaming channel, and the CLI uses the same interface.
- Storage: SQLite in WAL mode for the append-only, hash-chained event log and derived tables, plus one workspace folder per run. Derived tables can always be rebuilt from the log.
- Provider layer: one internal message model, Chat Completions and Responses adapters, per-server admin adapters (LM Studio native API, llama.cpp router, Unsloth) and a capability prober.
- Observability: the event schema is internal and versioned. An optional exporter maps it to OpenTelemetry GenAI conventions at a pinned version.

## 15. Run model

A run is an event-sourced state machine, so recovery, audit and the interface all read the same record.

### Entities

Provider, model, role profile, plan, plan version, task, binding constraint, run, task attempt (one session each), message, tool call, handoff, artifact, event, template and setting.

### Task and run states

&#91;embedded content: task states · 8 states, 1 optional gate\]

A finished task reopens only when an upstream task is rerun, which marks every downstream result stale. A failed task can be retried, skipped or end the run, depending on the failure policy. A user can cancel from any state before a task succeeds. Run states are queued, running, paused, succeeded, failed and cancelled.

### Handoff schema

| Field | Contents | Rule |
| --- | --- | --- |
| status | done, partial or blocked | Required |
| summary | What was done and the result, in plain prose | May be shortened, but is never the only record |
| key\_facts | Facts later tasks need | Each fact cites a message, tool result or file |
| binding\_constraints | Every active constraint with id, text, prerequisite, authority, fallback, state and resolution evidence | Copied verbatim from the plan or the previous handoff; a dropped or reworded constraint fails validation |
| decisions and assumptions | Choices made and what they rest on | Stored apart from facts |
| open\_issues | Anything unresolved | A blocking issue must name its constraint |
| deliverables | Workspace paths with content hashes | The hash must match the file |
| raw\_output\_ref | Pointer to the full output and transcript | Readable by a tool, never injected by default |
| usage | Tokens, time, model and parameters | Written by the engine, not the model |

Output that does not match the schema is repaired by a short follow-up in the same session - one round, plus a second on chains of six or more tasks - then fails the attempt. Constraint fields are checked by comparing ids and text hashes, never by asking a model. A constraint is released only by an explicit record (HIL-3), and a downstream prompt is never the only check.

### Event log

Every state change is an event with a run id, sequence number, timestamp, schema version, payload and the hash of the previous event. Event types include run created, task ready, attempt started, message added, tool called, tool denied, handoff recorded, gate approved, constraint released, task succeeded, task failed and run cancelled. Resuming replays the log, and a completed tool call that changed state is never repeated without confirmation. Each attempt has its own id, so a retry never overwrites an earlier transcript.

## 16. UX requirements

- Three working areas: goal and plan canvas, inspector, and run view; the same task selection drives all three.
- Every status is conveyed by text or icon as well as colour.
- Empty, error and loading states say what happened and what to do next.
- Long operations show progress and an estimate based on observed tokens per second.
- Destructive actions (rerun from here, delete data) state their consequences before confirming.

* Every canvas action has a keyboard and list-view equivalent, and no information depends on drag, hover or colour alone.
* Security state is always visible: each task shows its capability badge, its active binding constraints and its granted tools.

## 17. Success metrics

All targets are measured on the Phase 0 benchmark of 100 goals with the reference model, in beta feedback, or by automated tests. Nothing depends on telemetry, which is off by default.

| Metric | Target | How it is measured |
| --- | --- | --- |
| Install to first successful run, median | Under 10 minutes | Moderated sessions and beta timing |
| Plans valid on the first attempt | At least 90 percent | Benchmark |
| Plans valid after automatic repair | At least 99 percent | Benchmark |
| Runs completing without human help | At least 85 percent | Benchmark |
| Binding constraints preserved across handoffs | 100 percent on the seeded erosion suite | Deterministic validator over planted constraints, summaries and collectors |
| Forbidden actions in the injection corpus | Zero denied actions executed | 200-case corpus run against every release candidate |
| Resume after a forced kill | At least 99 percent of 1,000 kills | Fault-injection harness |
| Runs that exceed a configured cap | Zero, beyond one model call | Cap tests |
| Crash-free sessions | At least 99.5 percent | Beta feedback and opt-in crash reports |
| Usability score (SUS) | At least 80 | Study with 8 or more users |
| Accessibility | No critical WCAG 2.2 AA failures | External audit |
| Outbound connections in the default configuration | Zero | Automated network capture |
| Release integrity | 100 percent of installers signed, SBOM published for every release | Release checklist |
| Vulnerability handling | Acknowledge within 3 business days; actively exploited issues patched within 14 days | Security log |

## 18. Release plan

Delivery takes 28 to 36 weeks in six phases, and no phase starts until the previous gate has passed.

&#91;embedded content: roadmap · 6 phases, 6 gates\]

| Phase | The gate passes only when |
| --- | --- |
| 0. Foundations | Decision records for engine language, shell and licence are signed; the capability matrix covers LM Studio, Unsloth Studio, Ollama, llama.cpp and vLLM; plan and handoff schema v1 exist; the 100-goal benchmark and threat model are done; the prototype has been tested with 5 to 8 users |
| 1. Core engine | The fault-injection suite is green; both wire adapters pass recorded-conversation tests; the handoff validator passes the seeded erosion suite; installers build and sign in CI |
| 2. Builder and run experience | A study with at least 8 users scores 80 or higher on SUS; every drag action has a keyboard path; the context preview matches the real request exactly |
| 3. Tools and control | The injection corpus runs with zero denied actions executed; path-traversal and sandbox tests pass; the internal security review is closed |
| 4. Hardening | The external accessibility audit and penetration test have no open critical findings; all success metrics are met in a closed beta of about 50 users; documentation and the support policy are complete; counsel has signed off COM-3 |
| 5. General availability 1.0 | All P0 requirements are verified; the release candidate has run for one week with no new critical defects; signed installers and the SBOM are published |

After 1.0: parallel branches, automatic re-planning, the local API and hooks, run comparison, a plugin interface, and tracking of new Open Responses and MCP revisions.

## 19. Quality assurance and release engineering

### Testing

- Unit and integration tests against a deterministic mock server that speaks both Chat Completions and Responses, including streaming, tool calls, malformed output and stalls.
- Nightly contract tests against real LM Studio, Unsloth Studio, Ollama and llama.cpp using small models, plus the Open Responses compliance tests wherever a server claims support.
- Fault injection: kill the engine mid-task, drop the connection, return malformed JSON, fill the disk, change the system clock.
- A seeded erosion suite that plants binding constraints, then runs summaries, collectors and compaction and checks by code that none is dropped or reworded.
- A prompt-injection corpus of at least 200 cases in files, tool results and imported plans, checked against the permission broker's audit log.
- End-to-end interface tests, automated accessibility checks each build, a manual screen-reader pass, and an external WCAG 2.2 AA audit before GA.
- Planner and runtime evaluations on the 100-goal benchmark for every release candidate. A regression in any metric in section 17 blocks the release.
- A platform matrix across Windows, macOS and Linux, including a 16 GB RAM, CPU-only machine.

### Release pipeline

- Reproducible builds in CI, a software bill of materials in a standard format, dependency and licence scans, and provenance attestations for every artifact.
- Signing keys held in a managed key service or hardware module, never on build agents. Windows installers are signed with timestamps, macOS builds are signed, notarized and stapled, and certificate renewal is calendared because public code-signing certificates are now short-lived.
- The update manifest is signed by us and carries version, URL and hash. The client rejects downgrades and unsigned manifests, and the updater framework is pinned to a version that includes the downgrade fix.
- Channels: stable and beta. Staged rollout, a one-click rollback to the previous signed release, and data migrations that back up first.

### Support and maintenance

- Support policy: security fixes for at least five years after each release when distributed commercially in the EU; bug fixes for the current and previous minor release.
- Vulnerability handling: security contact and disclosure policy published, a named incident owner, and a tabletop exercise before GA for reporting under the Cyber Resilience Act.
- Compatibility watch: a monthly check of LM Studio, Unsloth Studio, llama.cpp, Open Responses and MCP releases, with adapter updates shipped in patch releases.
- Documentation, changelog and migration notes are release gates, not follow-ups.

## 20. Risks and mitigations

| Risk | Trigger | Mitigation | Owner |
| --- | --- | --- | --- |
| Small models give weak plans or handoffs | Benchmark plan validity under target on the reference model | Constrained decoding, repair loop, size caps, easy editing, prompt tuning driven by the benchmark | Engineering lead |
| Constraints erode between tasks | Seeded erosion suite fails | Structured binding constraints, verbatim copy, code validation, compaction never touches them | Engineering lead |
| Prompt injection through files or tools | Injection corpus shows any denied action executed | Least agency, Rule of Two linting, untrusted labels, approval gates | Security lead |
| Provider server runs its own tools | A server accepts enable\_tools or exposes MCP tools unasked | Never enable provider-side tools, send explicit off flags, warn the user | Security lead |
| Context overflow or very slow CPU inference | Runs stall or exceed time caps on the low-end machine | Token budgeting, compaction, progress estimates, per-task timeouts, model preloading | Engineering lead |
| Provider APIs change or differ (Unsloth Studio is Beta) | Nightly contract test fails | Adapter layer, capability probing, monthly compatibility watch, patch releases | Engineering lead |
| Specifications keep moving (MCP, Open Responses, OpenTelemetry GenAI) | A new revision or a breaking change | Internal canonical schemas, optional adapters, pinned versions | Architect |
| Updater or signing failure | Failed signature check, expired certificate, downgrade report | Own signed manifest with anti-rollback, calendared renewals, rollback procedure, key service | Release manager |
| Regulatory change (CRA, AI Act) or a different publisher role than assumed | Counsel advice or a new guidance document | Design-in controls from 1.0, quarterly regulatory watch, decisions OQ-4 and OQ-8 made in Phase 0 | Product manager |
| Accessibility of a node canvas | Audit finds keyboard or screen-reader gaps | List-view equivalent for every canvas action, early audits in Phase 2 | Design lead |
| Scope creep into a general agent platform | Requests for parallel agents, cloud sync or a marketplace | Principles and non-goals reviewed at each gate | Product manager |
| Cross-platform packaging and signing delays | Installer pipeline not green by the end of Phase 1 | Installer pipeline built in Phase 1, not Phase 4 | Release manager |

## 21. Decisions and open questions

### Decided by this research

- D1 Unsloth Studio is supported as a first-class preset. It exposes authenticated OpenAI-compatible endpoints, so the earlier open question is closed. It is marked Beta in the product, and the app always disables its server-side tools.
- D2 Two wire adapters (Chat Completions and Responses) sit behind one internal message model shaped like Responses items.
- D3 The engine is stateless toward servers and always sends full history.
- D4 Handoffs carry binding constraints as structured fields copied verbatim and validated by code. Default handoff content is a structured record, with full output readable through a tool.
- D5 Hard budgets are on by default for every run.
- D6 The Rule of Two is enforced by the plan linter.
- D7 MCP and OpenTelemetry export are optional adapters behind the internal event and tool interfaces.
- D8 Updates use our own signed manifest with anti-rollback, independent of the shell framework.

### Still open for Phase 0

- OQ-2 Engine language: TypeScript for one runtime, or Python for ecosystem reach.
- OQ-3 Shell: Tauri or Electron, decided by a canvas performance and accessibility test.
- OQ-4 Licence and distribution model (open source, source-available or commercial). It decides the product's role under the Cyber Resilience Act.
- OQ-5 Minimum supported hardware and the reference model for the benchmark.
- OQ-6 Plan structure: proposed is a dependency graph whose display order equals one deterministic execution order, with drag-to-reorder where valid.
- OQ-7 Platform priority and code-signing arrangements.
- OQ-8 Whether the product is distributed in the EU, which decides whether COM-1 and COM-2 are mandatory from day one.
- OQ-9 Crash-report policy: none, or opt-in with a published privacy notice.
- OQ-10 Whether to read model licences from server metadata or from the model repository.

## 22. Sources

Checked November 20, 2026. Pages marked opened were read in full; the others were read as search-result excerpts, so their figures and dates should be re-checked in Phase 0 before they drive a decision.

### Opened

- [How to use Unsloth as an API endpoint](https://unsloth.ai/docs/basics/api.md): endpoints, keys, ports, server-side tool policy.
- [LM Studio REST API overview](https://lmstudio.ai/docs/developer/rest): native v1 API, authentication, model load and unload, endpoint comparison.
- [When Must Becomes Maybe: erosion of workflow constraints in LLM agent handoffs](https://arxiv.org/pdf/2608.24569): handoff erosion and restoration results.
- [CVE-2026-95625 record](https://osv.dev/vulnerability/CVE-2026-95625): updater downgrade weakness and its fixed version.

### Read as excerpts

- Model servers: [LM Studio structured output](https://www.lmstudio.ai/docs/developer/openai-compat/structured-output), [llama.cpp router mode](https://dev.to/rosgluk/unload-all-llamacpp-router-models-without-restarting-8dj).
- Standards: [Open Responses donation](https://thenewstack.io/openai-donates-open-responses/), [Open Responses specification](https://www.openresponses.org/llms.txt), [MCP 2026-07-28 changes](https://workos.com/blog/mcp-2026-spec-agent-authentication), [MCP revision and authorization](https://www.descope.com/blog/post/july-2026-mcp-revision), [OpenTelemetry GenAI conventions](https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained).
- Agent reliability and security: [multi-agent failure analysis](https://www.oreilly.com/radar/?p=18124), [context compaction and safety constraints](https://arxiv.org/pdf/2606.22528), [Agents Rule of Two](https://simonwillison.net/2025/Nov/2/new-prompt-injection-papers/), [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-application), [OWASP list summary](https://www.techrepublic.com/article/news-owasp-ai-agent-risk-list/).
- Regulation: [Cyber Resilience Act guidance](https://www.orrick.com/en/Insights/2026/09/EU-Cyber-Resilience-Act-New-Guidance-Clarifies-Scope-Reporting), [CRA preparation](https://www.goodwinlaw.com/en/insights/publications/2026/09/alerts-lifesciences-technology-preparing-for-eu-cyber-resilience-act), [Digital Omnibus on AI in force](https://natlawreview.com/article/eu-digital-omnibus-ai-enters-force), [AI Act delay agreement](https://www.hoganlovells.com/en/publications/eu-legislators-agree-to-delay-for-highrisk-ai-rules), [AI Act transparency and risk guidance](https://www.mondaq.com/intellectual-property/1825624/eu-ai-act-news-digital-omnibus-on-ai-new-guidance-on-risk-classification-gpai-and-transparency-obligations).
- Release engineering: [code-signing certificate lifetimes](https://www.ssl.com/article/reminder-reduced-code-signing-certificate-lifecycles/amp/), [Artifact Signing](https://techcommunity.microsoft.com/blog/microsoft-security-blog/-/4482789).

## Appendix A. Prototype gaps

The current agent\_studio.py is a single-file proof of concept that has not yet been run against a live model server. It lacks: durable runs and resume, streaming, capability probing, structured handoffs, workspace tools and permissions, drag-and-drop wiring, run history and comparison, context budgeting, retries and loop detection, tests, packaging, accessibility work and a security model beyond a request header check. Its planner, sequential scheduler and editable task flow are the starting point for the 1.0 design.
