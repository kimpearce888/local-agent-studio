//! The independent agent runtime (mandate 6.2): one tokio actor per
//! active agent, durable inboxes, per-session turn locks, busy-agent
//! delivery modes, and restart-safe delivery (anything not yet
//! delivered is re-delivered when the runtime restarts).
//!
//! Model access is injected as a `Responder` so tests run without a
//! server; the inference scheduler (scheduler.rs) is the production
//! responder front-end.

use crate::agents::AgentStatus;
use crate::communication::{BusyMode, DeliveryState};
use crate::storage::{utc_now, CoreStore, InboxRow, MessageRow};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tokio::sync::{mpsc, oneshot};

use std::future::Future;
use std::pin::Pin;

/// How an agent answers a delivered item: an async factory so
/// production can await the shared inference scheduler and the
/// provider HTTP call. Tests provide deterministic replies.
pub type AsyncResponder = Arc<
    dyn Fn(&InboxRow) -> Pin<Box<dyn Future<Output = String> + Send>>
        + Send + Sync + 'static>;

/// The production responder: resolve the agent's current profile,
/// take a fair scheduler slot for its (provider, model), then run the
/// blocking HTTP chat on the blocking pool. Scheduler fairness and
/// bounded concurrency therefore apply to every real model turn.
pub fn production_responder(
    store: Arc<CoreStore>,
    scheduler: Arc<crate::scheduler::InferenceScheduler>,
    adapters: Arc<crate::providers::AdapterRegistry>,
    mcp: crate::mcp::McpClients,
) -> AsyncResponder {
    Arc::new(move |entry: &InboxRow| {
        let store = store.clone();
        let scheduler = scheduler.clone();
        let adapters = adapters.clone();
        let mcp = mcp.clone();
        let recipient = entry.recipient.clone();
        let payload = entry.payload_json.clone();
        Box::pin(async move {
            let agent = match store.agent(&recipient) {
                Ok(a) => a,
                Err(_) => return "(agent unavailable)".into(),
            };
            let provider = agent.profile.model_provider.clone();
            let model = agent.profile.model_id.clone();
            let adapter = match adapters.get(&provider) {
                Some(a) => a,
                None => return format!(
                    "(no adapter registered for {provider})"),
            };
            let key = crate::scheduler::ModelKey {
                provider: provider.clone(), model: model.clone() };
            let _permit = scheduler.acquire(key, &recipient).await;
            let prompt = serde_json::from_str::<serde_json::Value>(
                &payload).ok()
                .and_then(|v| v.get("content").and_then(
                    |c| c.as_str()).map(str::to_string))
                .unwrap_or_else(|| payload.clone());
            let params: serde_json::Value =
                serde_json::from_str(&agent.profile.params_json)
                    .unwrap_or(serde_json::Value::Null);
            // Token-budgeted context assembly (mandate 9): memory +
            // recent transcript + the inbound message. The actor has
            // already appended the inbound to the transcript, so the
            // last user row IS this message - drop it (the contract
            // is documented in context::assemble_context).
            let session = crate::sessions::current_session(
                &store, &recipient).id;
            let memories: Vec<String> = store
                .list_memory(&recipient)
                .map(|ms| ms.into_iter()
                     .map(|m| m.content).collect())
                .unwrap_or_default();
            let mut transcript: Vec<(String, String)> = store
                .transcript(&session)
                .map(|ts| ts.into_iter()
                     .map(|m| (m.role, m.content)).collect())
                .unwrap_or_default();
            if transcript.last().map(|(r, _)| r.as_str())
                == Some("user") {
                transcript.pop();
            }
            let raw_budget = params.get("context_budget")
                .and_then(|v| v.as_u64())
                .map(|v| v as usize)
                .unwrap_or(crate::context::
                           DEFAULT_CONTEXT_BUDGET_TOKENS);
            // Mandate 9.3: the provider's own token counts calibrate
            // the budget (see context::calibrate) and every call's
            // exact usage is persisted with our pre-call estimate.
            let last = store.last_token_usage(
                &recipient, &provider, &model).ok().flatten();
            let (budget, _basis) =
                crate::context::calibrate(raw_budget, last);
            // Mandate 13.2: the agent's granted MCP tools and
            // their schemas enter the turn's system context (the
            // note is empty when no server is granted).
            let grants = crate::tools::parse_grants(
                &agent.profile.tool_grants_json);
            let catalog = mcp.tool_catalog(
                &recipient, &grants);
            let system = {
                let note = crate::mcp::mcp_tools_note(&catalog);
                if note.is_empty() {
                    agent.profile.system_prompt.clone()
                } else {
                    format!("{}\n\n{}",
                            agent.profile.system_prompt, note)
                }
            };
            let messages = crate::context::assemble_context(
                &system, &memories,
                &transcript, &prompt, budget);
            let pre_call_estimate: i64 = messages.iter()
                .map(|m| crate::context::estimate_tokens(&m.content)
                     as i64).sum();
            let chat_model = model.clone();
            let call = tokio::task::spawn_blocking(move || {
                adapter.chat(&chat_model, &messages, &params)
            }).await;
            match call {
                Ok(Ok(result)) => {
                    if result.tokens_in > 0 {
                        let _ = store.record_token_usage(
                            &recipient, &provider, &model,
                            result.tokens_in, result.tokens_out,
                            pre_call_estimate);
                    }
                    result.content
                }
                Ok(Err(e)) => format!("(provider error: {e})"),
                Err(e) => format!("(join error: {e})"),
            }
        })
    })
}

pub enum AgentCommand {
    /// Hand one persisted inbox entry to the agent actor.
    Deliver(InboxRow, Option<oneshot::Sender<DeliveryState>>),
    /// Stop the actor; persisted state stays durable.
    Shutdown,
}

struct Actor {
    agent_id: String,
    store: Arc<CoreStore>,
    responder: AsyncResponder,
    /// Shared MCP registry (mandate 13.2): cloned into every turn's
    /// broker so MCP tool calls route through the same grants.
    mcp: crate::mcp::McpClients,
    /// Per-session turn locks: a session never runs two conflicting
    /// turns simultaneously (mandate 6.2). tokio Mutex: the guard is
    /// held across the model-call await and must be Send.
    turn_locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

impl Actor {
    /// The broker for this agent's turn: workspace jail under the
    /// data dir, explicit grants from the persisted profile, web
    /// domain allowlist from profile params.  An in-memory store
    /// has no data dir, so file tools have no jail and are denied
    /// honestly; web_fetch can still run with its allowlist.
    fn tool_broker(&self) -> crate::tools::Broker {
        let agent = self.store.agent(&self.agent_id)
            .map(|a| a.profile).ok();
        let grants: Vec<crate::tools::ToolGrant> = agent.as_ref()
            .map(|p| crate::tools::parse_grants(
                &p.tool_grants_json))
            .unwrap_or_default();
        let one_shot: Vec<String> = self.store
            .one_shot_grants(&self.agent_id)
            .unwrap_or_default();
        let params: serde_json::Value = agent.as_ref()
            .and_then(|p| serde_json::from_str(&p.params_json).ok())
            .unwrap_or(serde_json::Value::Null);
        let domains: Vec<String> = params.get("web_domains")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|d| d.as_str()
                     .map(str::to_string)).collect())
            .unwrap_or_default();
        let jail = self.store.data_dir()
            .map(|d| d.join("workspaces").join(&self.agent_id))
            .unwrap_or_else(|| {
                // In-memory store: a jail that cannot exist.
                std::path::PathBuf::from(
                    "/nonexistent-las-in-memory-store")
            });
        let _ = std::fs::create_dir_all(&jail);
        let commands: Vec<String> = params.get("command_allowlist")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|c|
                c.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        crate::tools::Broker::new(jail, grants)
            .with_one_shot(one_shot)
            .with_web_domains(domains)
            .with_command_allowlist(commands)
            .with_mcp(self.mcp.clone(), self.agent_id.clone())
    }

    fn lock_for(&self, session: &str) -> Arc<tokio::sync::Mutex<()>> {
        let mut locks = self.turn_locks.lock().unwrap();
        locks.entry(session.into()).or_default().clone()
    }

    async fn run(self, mut rx: mpsc::UnboundedReceiver<AgentCommand>) {
        while let Some(cmd) = rx.recv().await {
            match cmd {
                AgentCommand::Shutdown => break,
                AgentCommand::Deliver(entry, ack) => {
                    let state = self.process(entry).await;
                    if let Some(ack) = ack {
                        let _ = ack.send(state);
                    }
                }
            }
        }
    }

    /// Deliver at a turn boundary (mandate 7.2): mark in-flight, take
    /// the session turn lock, append to the canonical transcript,
    /// reply through the responder, and advance the delivery state.
    async fn process(&self, entry: InboxRow) -> DeliveryState {
        let _ = self.store.update_delivery(
            entry.id, DeliveryState::Queued.as_str(), &utc_now());
        let _ = self.store.set_status(
            &self.agent_id, AgentStatus::Working);
        // The active segment (find-or-create `-s-main` on first
        // delivery, restart-safe), followed by the rollover check
        // (mandate 9.4): when the PRIOR transcript has outgrown the
        // agent's policy, roll over BEFORE the inbound is appended,
        // so the inbound lands in the next segment where the logical
        // conversation continues.
        let mut session = crate::sessions::current_session(
            &self.store, &self.agent_id);
        let params: serde_json::Value = self.store
            .agent(&self.agent_id).ok()
            .and_then(|a| serde_json::from_str(
                &a.profile.params_json).ok())
            .unwrap_or(serde_json::Value::Null);
        let policy = crate::sessions::RolloverPolicy::from_params(
            &params);
        if let Ok(prior_messages) = self.store.transcript(&session.id) {
            if crate::sessions::needs_rollover(
                &prior_messages, &policy) {
                if let Ok(next) = crate::sessions::perform_rollover(
                    &self.store, &self.agent_id, &prior_messages) {
                    session = next;
                }
            }
        }
        let session = session.id;   // downstream works with the id
        let lock = self.lock_for(&session);
        let _guard = lock.lock().await;

        let _ = self.store.update_delivery(
            entry.id, DeliveryState::InFlight.as_str(), &utc_now());
        let _ = self.store.append_message(&MessageRow {
            session_id: session.clone(), seq: 0,   // SQL-allocated
            role: "user".into(),
            content: format!("[{}] from {}: {}",
                             entry.kind, entry.sender, entry.payload_json),
            reasoning: String::new(), tool_json: String::new(),
            model: String::new(), ts: utc_now(),
        });

        // The model reply: scheduler + provider adapter in production,
        // deterministic closures in tests.  A reply may carry
        // TOOL_CALL lines (the provider-agnostic convention in
        // tools.rs): each call goes through the permission broker,
        // the result is appended to the transcript, and the model
        // gets another round - bounded by MAX_TOOL_ROUNDS and
        // MAX_TOOL_CALLS_PER_TURN (spec 5.9 rate limit).
        let broker = self.tool_broker();
        let mut reply = (self.responder)(&entry).await;
        let mut calls_used = 0usize;
        for _round in 0..crate::tools::MAX_TOOL_ROUNDS {
            let calls = crate::tools::parse_tool_calls(&reply);
            if calls.is_empty() {
                break;   // a final answer, not a tool turn
            }
            // The assistant turn that asked for tools is part of
            // the record.
            let _ = self.store.append_message(&MessageRow {
                session_id: session.clone(), seq: 0,
                role: "assistant".into(), content: reply.clone(),
                reasoning: String::new(), tool_json: String::new(),
                model: String::new(), ts: utc_now(),
            });
            for call in &calls {
                if calls_used
                    >= crate::tools::MAX_TOOL_CALLS_PER_TURN {
                    let _ = self.store.log_tool_event(
                        &self.agent_id, &call.tool, "tool.denied",
                        r#"{"reason":"per-turn call budget                            exhausted"}"#);
                    let _ = self.store.append_message(&MessageRow {
                        session_id: session.clone(), seq: 0,
                        role: "tool".into(),
                        content: format!(
                            "tool {} -> error:\ndenied: per-turn \
                             call budget exhausted", call.tool),
                        reasoning: String::new(),
                        tool_json: String::new(),
                        model: String::new(), ts: utc_now(),
                    });
                    continue;
                }
                calls_used += 1;
                // A granted-but-ask-mode call is DEFERRED: record
                // the approval request so the Tools screen can
                // surface it, audit the deferral, tell the model.
                let ask = matches!(
                    broker.authorize(call),
                    crate::tools::Decision::Ask(_));
                if ask {
                    let _ = self.store.request_tool_approval(
                        &self.agent_id, &call.tool,
                        &call.args.to_string());
                }
                let outcome = broker.execute(call);
                let deferred = outcome.output.starts_with(
                    "deferred:");
                let ran_ok = outcome.ok && !deferred;
                let decision = if ran_ok {
                    "tool.called"
                } else {
                    "tool.denied"
                };
                if ran_ok {
                    // A one-shot approval paid for exactly this
                    // execution; consume it so the next use defers
                    // again.
                    let _ = self.store.consume_one_shot_grant(
                        &self.agent_id, &call.tool);
                }
                let detail = serde_json::json!({
                    "tool": call.tool, "args": call.args,
                    "ok": outcome.ok}).to_string();
                let _ = self.store.log_tool_event(
                    &self.agent_id, &call.tool, decision, &detail);
                let _ = self.store.append_message(&MessageRow {
                    session_id: session.clone(), seq: 0,
                    role: "tool".into(),
                    content: crate::tools::format_tool_result(
                        call, &outcome),
                    reasoning: String::new(),
                    tool_json: String::new(),
                    model: String::new(), ts: utc_now(),
                });
            }
            // The next round sees the tool results through the
            // transcript (the responder re-assembles context).
            reply = (self.responder)(&entry).await;
        }
        let _ = self.store.append_message(&MessageRow {
            session_id: session.clone(), seq: 0,   // SQL-allocated
            role: "assistant".into(), content: reply.clone(),
            reasoning: String::new(), tool_json: String::new(),
            model: String::new(), ts: utc_now(),
        });

        // Reply routing (mandate 7): when the sender is another
        // agent, the reply becomes a NEW inbox entry for them -
        // persisting it IS the routing, and send_message's topology
        // check applies for real (a missing reverse rule refuses the
        // reply and the refusal is recorded, never silently lost).
        if entry.kind == "message" && entry.sender != "user" {
            match self.store.send_message(
                &self.agent_id, &entry.sender, &reply, "",
                crate::communication::BusyMode::Queue) {
                Ok(_) => {}
                Err(e) => {
                    let _ = self.store.append_message(&MessageRow {
                        session_id: session.clone(), seq: 0,
                        role: "system".into(),
                        content: format!(
                            "reply to {} refused by the topology: \
                             {e}", entry.sender),
                        reasoning: String::new(),
                        tool_json: String::new(),
                        model: String::new(), ts: utc_now(),
                    });
                }
            }
        }
        let _ = self.store.update_delivery(
            entry.id, DeliveryState::Delivered.as_str(), &utc_now());
        let _ = self.store.update_delivery(
            entry.id, DeliveryState::Acked.as_str(), &utc_now());
        let _ = self.store.set_status(&self.agent_id, AgentStatus::Idle);
        DeliveryState::Acked
    }
}

/// The multi-agent runtime. Actors start on demand and stop on
/// `shutdown`; persistence is the source of truth, so a restart
/// reconstructs everything (`recover` re-delivers pending entries).
pub struct AgentRuntime {
    store: Arc<CoreStore>,
    responder: AsyncResponder,
    senders: Mutex<HashMap<String, mpsc::UnboundedSender<AgentCommand>>>,
    mcp: crate::mcp::McpClients,
}

impl AgentRuntime {
    pub fn new(store: Arc<CoreStore>, responder: AsyncResponder) -> Self {
        let mcp = crate::mcp::McpClients::new(store.clone());
        Self { store, responder,
               senders: Mutex::new(HashMap::new()),
               mcp }
    }

    fn sender_for(&self, agent_id: &str)
                  -> mpsc::UnboundedSender<AgentCommand> {
        let mut senders = self.senders.lock().unwrap();
        if let Some(tx) = senders.get(agent_id) {
            return tx.clone();
        }
        let (tx, rx) = mpsc::unbounded_channel();
        let actor = Actor {
            agent_id: agent_id.into(),
            store: self.store.clone(),
            responder: self.responder.clone(),
            mcp: self.mcp.clone(),
            turn_locks: Mutex::new(HashMap::new()),
        };
        tokio::spawn(actor.run(rx));
        senders.insert(agent_id.into(), tx.clone());
        tx
    }

    /// Deliver a persisted inbox entry to its recipient.
    ///
    /// - Queue/Async/Steer/Interrupt: fire-and-forget after the row is
    ///   durably Accepted (the mode is recorded; Steer/Interrupt
    ///   boundaries apply inside `process`).
    /// - Wait: resolves when the recipient acknowledges.
    pub async fn deliver(&self, entry: &InboxRow)
                         -> std::result::Result<DeliveryState, String> {
        let mode = BusyMode::parse(&entry.busy_mode);
        let tx = self.sender_for(&entry.recipient);
        if mode == BusyMode::Wait {
            let (ack_tx, ack_rx) = oneshot::channel();
            tx.send(AgentCommand::Deliver(entry.clone(), Some(ack_tx)))
                .map_err(|e| e.to_string())?;
            ack_rx.await.map_err(|e| e.to_string())
        } else {
            tx.send(AgentCommand::Deliver(entry.clone(), None))
                .map_err(|e| e.to_string())?;
            Ok(DeliveryState::Queued)
        }
    }

    /// Restart recovery: every inbox entry not yet delivered is
    /// re-delivered (mandate 6.2: restarts reconstruct state).
    pub async fn recover(&self) -> usize {
        let mut pending = Vec::new();
        for agent in self.store.list_agents(true).unwrap_or_default() {
            if !agent.enabled || agent.archived {
                continue;   // disabled/archived agents receive nothing
            }
            for entry in self.store.inbox_for(&agent.id)
                .unwrap_or_default() {
                if matches!(entry.delivery_state.as_str(),
                            "accepted" | "queued") {
                    pending.push(entry);
                }
            }
        }
        let n = pending.len();
        for entry in pending {
            let _ = self.deliver(&entry).await;
        }
        n
    }

    pub fn shutdown(&self) {
        let senders = self.senders.lock().unwrap();
        for tx in senders.values() {
            let _ = tx.send(AgentCommand::Shutdown);
        }
    }
}

#[cfg(test)]
mod tests {
    fn echo_responder() -> AsyncResponder {
        // Deterministic: the reply names the recipient, so routing
        // is observable in the sender's inbox.
        Arc::new(|entry: &crate::storage::InboxRow| {
            let text = format!(
                "reply from {} to {}: {}",
                entry.recipient, entry.sender,
                entry.payload_json);
            Box::pin(async move { text })
        })
    }

    #[tokio::test]
    async fn replies_route_back_to_the_sender_agent() {
        let store = store_with_agents();
        // Bidirectional topology: a -> b and b -> a.
        for (src, dst) in [("a", "b"), ("b", "a")] {
            store.set_comm_rule(&crate::storage::CommRuleRow {
                src: src.into(), dst: dst.into(),
                may_message: true, may_call: true,
                may_delegate: true,
            }).unwrap();
        }
        let rt = AgentRuntime::new(
            store.clone(), echo_responder());
        let entry = store.send_message(
            "a", "b", "ping", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();
        // b answered; the reply is a NEW inbox entry for a.
        let a_inbox = store.inbox_for("a").unwrap();
        assert_eq!(a_inbox.len(), 1,
                   "the reply routed back to the sender");
        assert!(a_inbox[0].payload_json.contains("reply from b"),
                "routed payload is the model reply: {}",
                a_inbox[0].payload_json);
        // Deliver a's view of the reply: a answers too, closing the
        // loop (the conversation continues turn by turn).
        let mut rt2_entry = a_inbox[0].clone();
        rt2_entry.busy_mode = BusyMode::Wait.as_str().into();
        rt.deliver(&rt2_entry).await.unwrap();
        let b_inbox = store.inbox_for("b").unwrap();
        assert_eq!(b_inbox.len(), 2,
                   "second round-trip routed again");
        rt.shutdown();
    }

    #[tokio::test]
    async fn tool_calls_execute_through_the_broker() {
        // File-backed store: the workspace jail lives under its dir.
        let dir = std::env::temp_dir().join(format!(
            "las-toolloop-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let store = Arc::new(
            CoreStore::open(dir.join("t.db")).unwrap());
        let mut spec = spec("w");
        spec.tool_grants = vec!["write_file".into(),
                                "read_file".into()];
        store.create_agent("w", "w", "", &spec).unwrap();
        // Stateful responder: the first turn asks for a tool, the
        // second sees the tool result in the re-assembled context
        // and gives the final answer.
        let round = std::sync::atomic::AtomicUsize::new(0);
        let responder: AsyncResponder = Arc::new(
            move |_entry: &crate::storage::InboxRow| {
                let n = round.fetch_add(
                    1, std::sync::atomic::Ordering::SeqCst);
                let text = if n == 0 {
                    "I will write the plan.\nTOOL_CALL {\"tool\": \
                     \"write_file\", \"args\": {\"path\": \
                     \"plan.md\", \"content\": \"step 1\"}}"
                        .to_string()
                } else {
                    "The plan is written.".to_string()
                };
                Box::pin(async move { text })
            });
        let rt = AgentRuntime::new(store.clone(), responder);
        let entry = store.send_message(
            "user", "w", "make a plan", "", BusyMode::Wait)
            .unwrap();
        rt.deliver(&entry).await.unwrap();
        // Transcript: inbound, the tool-request turn, the tool
        // result, then the final answer.
        let t = store.transcript("w-s-main").unwrap();
        let roles: Vec<&str> = t.iter()
            .map(|m| m.role.as_str()).collect();
        assert_eq!(roles,
                   vec!["user", "assistant", "tool", "assistant"],
                   "the tool round is part of the record");
        // The file really exists inside the jail.
        let ws = dir.join("workspaces").join("w");
        assert_eq!(
            std::fs::read_to_string(ws.join("plan.md")).unwrap(),
            "step 1", "the write really executed");
        // Audit: one authorized call.
        let audit = store.tool_events("w").unwrap();
        assert_eq!(audit.len(), 1);
        assert_eq!(audit[0].1, "write_file");
        assert_eq!(audit[0].2, "tool.called");
        rt.shutdown();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn mcp_tool_call_runs_through_the_loop() {
        // Mandate 13.2 end to end: a model turn asks for an MCP
        // tool, the broker checks the grant, the stdio server runs,
        // the untrusted-labelled result lands in the transcript and
        // the audit records both the connection and the call.
        let dir = std::env::temp_dir().join(format!(
            "las-mcploop-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let store = Arc::new(
            CoreStore::open(dir.join("t.db")).unwrap());
        // Register + approve the server through the same store the
        // runtime reads (AgentRuntime builds its registry from it).
        crate::mcp::McpClients::new(store.clone()).upsert(
            &crate::mcp::McpServerConfig {
                id: "docs".into(),
                command: "python3".into(),
                url: None,
                args: vec![
                    "tests/fixtures/mcp_echo_server.py".into()],
                env: Default::default(),
                allow_tools: vec!["*".into()],
            }).unwrap();
        crate::mcp::McpClients::new(store.clone())
            .set_enabled("docs", true).unwrap();
        let mut spec = spec("m");
        spec.tool_grants = vec!["mcp:docs".into()];
        store.create_agent("m", "m", "", &spec).unwrap();
        let round = std::sync::atomic::AtomicUsize::new(0);
        let responder: AsyncResponder = Arc::new(
            move |_entry: &crate::storage::InboxRow| {
                let n = round.fetch_add(
                    1, std::sync::atomic::Ordering::SeqCst);
                let text = if n == 0 {
                    "Let me use the docs server.\nTOOL_CALL \
                     {\"tool\": \"mcp:docs:echo\", \"args\": \
                     {\"text\": \"hey\"}}".to_string()
                } else {
                    "The echo came back.".to_string()
                };
                Box::pin(async move { text })
            });
        let rt = AgentRuntime::new(store.clone(), responder);
        let entry = store.send_message(
            "user", "m", "echo something", "", BusyMode::Wait)
            .unwrap();
        rt.deliver(&entry).await.unwrap();
        let t = store.transcript("m-s-main").unwrap();
        let roles: Vec<&str> = t.iter()
            .map(|m| m.role.as_str()).collect();
        assert_eq!(roles,
                   vec!["user", "assistant", "tool", "assistant"],
                   "the MCP tool round is part of the record");
        assert!(t[2].content.contains("hey!"),
                "the echo result reached the model: {}",
                t[2].content);
        assert!(t[2].content.contains("UNTRUSTED content"),
                "the result is labelled: {}", t[2].content);
        // Audit: the connection AND the authorized call.
        let audit = store.tool_events("m").unwrap();
        let decisions: Vec<String> = audit.iter()
            .map(|(_, tool, d, _)| format!("{tool}:{d}"))
            .collect();
        assert!(decisions.iter().any(|d| d == "mcp:docs:mcp.connected"),
                "{decisions:?}");
        assert!(decisions.iter().any(|d|
            d == "mcp:docs:echo:tool.called"), "{decisions:?}");
        rt.shutdown();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn ask_once_tool_defers_then_runs_after_approval() {
        let dir = std::env::temp_dir().join(format!(
            "las-askonce-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let store = Arc::new(
            CoreStore::open(dir.join("t.db")).unwrap());
        store.create_agent("a", "A", "", &spec("a")).unwrap();
        // The grant is ask_once: granted, but gated.
        store.set_tool_grants("a",
            r#"[{"tool":"read_file","mode":"ask_once"}]"#).unwrap();
        // A real file in the workspace jail.
        let ws = dir.join("workspaces").join("a");
        std::fs::create_dir_all(&ws).unwrap();
        std::fs::write(ws.join("note.md"), "content").unwrap();
        // Even TOOL_CALL turns, odd turns answer.
        let round = std::sync::atomic::AtomicUsize::new(0);
        let responder: AsyncResponder = Arc::new(
            move |_e: &crate::storage::InboxRow| {
                let n = round.fetch_add(
                    1, std::sync::atomic::Ordering::SeqCst);
                let text = if n % 2 == 0 {
                    "TOOL_CALL {\"tool\": \"read_file\", \
                     \"args\": {\"path\": \"note.md\"}}"
                        .to_string()
                } else {
                    "done".to_string()
                };
                Box::pin(async move { text })
            });
        let rt = AgentRuntime::new(store.clone(), responder);
        // Turn 1: the call is DEFERRED, not executed.
        let entry = store.send_message(
            "user", "a", "read it", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();
        let audit = store.tool_events("a").unwrap();
        assert_eq!(audit[0].2, "tool.denied");
        let t = store.transcript("a-s-main").unwrap();
        assert!(t.iter().any(|m| m.role == "tool"
            && m.content.contains("deferred:")),
            "the model SEES the deferral");
        let pending = store.pending_approvals().unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].2, "read_file");
        // The user approves on the Tools screen path.
        store.approve_tool_request(pending[0].0).unwrap();
        // The ask_once grant became always.
        let profile = store.agent("a").unwrap().profile;
        assert!(profile.tool_grants_json.contains(
            "\"mode\":\"always\""),
            "upgraded: {}", profile.tool_grants_json);
        // Turn 2: the same call now runs for real.
        let entry = store.send_message(
            "user", "a", "again", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();
        let audit = store.tool_events("a").unwrap();
        assert_eq!(audit[1].2, "tool.called",
                   "the approved call executed");
        let t = store.transcript("a-s-main").unwrap();
        assert!(t.iter().any(|m| m.role == "tool"
            && m.content.contains("content")),
            "the file content came back");
        assert!(store.pending_approvals().unwrap().is_empty());
        rt.shutdown();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn ask_each_grant_pays_for_exactly_one_use() {
        let dir = std::env::temp_dir().join(format!(
            "las-askche-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let store = Arc::new(
            CoreStore::open(dir.join("t.db")).unwrap());
        store.create_agent("a", "A", "", &spec("a")).unwrap();
        store.set_tool_grants("a",
            r#"[{"tool":"read_file","mode":"ask_each"}]"#).unwrap();
        let ws = dir.join("workspaces").join("a");
        std::fs::create_dir_all(&ws).unwrap();
        std::fs::write(ws.join("note.md"), "x").unwrap();
        let round = std::sync::atomic::AtomicUsize::new(0);
        let responder: AsyncResponder = Arc::new(
            move |_e: &crate::storage::InboxRow| {
                let n = round.fetch_add(
                    1, std::sync::atomic::Ordering::SeqCst);
                let text = if n % 2 == 0 {
                    "TOOL_CALL {\"tool\": \"read_file\", \
                     \"args\": {\"path\": \"note.md\"}}"
                        .to_string()
                } else {
                    "done".to_string()
                };
                Box::pin(async move { text })
            });
        let rt = AgentRuntime::new(store.clone(), responder);
        // Turn 1: deferred, request recorded.
        let e = store.send_message(
            "user", "a", "go", "", BusyMode::Wait).unwrap();
        rt.deliver(&e).await.unwrap();
        let pending = store.pending_approvals().unwrap();
        assert_eq!(pending.len(), 1);
        store.approve_tool_request(pending[0].0).unwrap();
        assert_eq!(store.one_shot_grants("a").unwrap(),
                   vec!["read_file".to_string()]);
        // Turn 2: the one-shot runs and is consumed.
        let e = store.send_message(
            "user", "a", "again", "", BusyMode::Wait).unwrap();
        rt.deliver(&e).await.unwrap();
        let audit = store.tool_events("a").unwrap();
        assert_eq!(audit[1].2, "tool.called");
        assert!(store.one_shot_grants("a").unwrap().is_empty(),
                "the one-shot was consumed by the real execution");
        // Turn 3: the same call defers again - ask EACH means
        // every use needs its own approval.
        let e = store.send_message(
            "user", "a", "once more", "", BusyMode::Wait).unwrap();
        rt.deliver(&e).await.unwrap();
        let audit = store.tool_events("a").unwrap();
        assert_eq!(audit[2].2, "tool.denied");
        assert_eq!(store.pending_approvals().unwrap().len(), 1);
        rt.shutdown();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn ungranted_tool_call_is_denied_and_audited() {
        let store = store_with_agents();   // no grants at all
        let round = std::sync::atomic::AtomicUsize::new(0);
        let responder: AsyncResponder = Arc::new(
            move |_entry: &crate::storage::InboxRow| {
                let n = round.fetch_add(
                    1, std::sync::atomic::Ordering::SeqCst);
                let text = if n == 0 {
                    "TOOL_CALL {\"tool\": \"write_file\", \
                     \"args\": {\"path\": \"x.md\", \
                     \"content\": \"nope\"}}".to_string()
                } else {
                    "I cannot write files.".to_string()
                };
                Box::pin(async move { text })
            });
        let rt = AgentRuntime::new(store.clone(), responder);
        let entry = store.send_message(
            "user", "a", "write x", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();
        let audit = store.tool_events("a").unwrap();
        assert_eq!(audit.len(), 1);
        assert_eq!(audit[0].2, "tool.denied",
                   "the refusal is audited");
        let t = store.transcript("a-s-main").unwrap();
        let tool_msg = t.iter().find(|m| m.role == "tool").unwrap();
        assert!(tool_msg.content.contains("denied:"),
                "the model SEES the denial: {}",
                tool_msg.content);
        // Nothing was written anywhere.
        assert!(!std::path::Path::new("x.md").exists());
        rt.shutdown();
    }

    #[tokio::test]
    async fn refused_reply_is_recorded_not_silently_lost() {
        let store = store_with_agents();
        // ONE-WAY topology: a -> b only (b -> a is default-deny).
        store.set_comm_rule(&crate::storage::CommRuleRow {
            src: "a".into(), dst: "b".into(),
            may_message: true, may_call: true,
            may_delegate: true,
        }).unwrap();
        let rt = AgentRuntime::new(
            store.clone(), echo_responder());
        let entry = store.send_message(
            "a", "b", "ping", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();
        // b tried to answer; the topology refused the reverse hop.
        assert!(store.inbox_for("a").unwrap().is_empty(),
                "no reverse hop without a rule");
        let transcript = store.transcript("b-s-main").unwrap();
        assert!(transcript.iter().any(|m| m.role == "system"
            && m.content.contains("refused by the topology")),
                "the refusal is recorded in b's transcript: {:?}",
                transcript.iter().map(|m| m.role.clone())
                    .collect::<Vec<_>>());
        rt.shutdown();
    }

    use super::*;
    use crate::agents::{ProfileSpec, RetentionPolicy};

    fn spec(name: &str) -> ProfileSpec {
        ProfileSpec {
            system_prompt: format!("You are {name}."),
            model_provider: "mock".into(), model_id: "m".into(),
            params: serde_json::Value::Null, tool_grants: vec![],
        }
    }

    fn store_with_agents() -> Arc<CoreStore> {
        let s = Arc::new(CoreStore::in_memory().unwrap());
        for id in ["a", "b"] {
            s.create_agent(id, id, "", &spec(id)).unwrap();
        }
        s
    }

    #[tokio::test]
    async fn delivery_advances_states_and_transcripts() {
        let store = store_with_agents();
        let entry = store.send_message(
            "user", "a", "hello", "k1", BusyMode::Queue).unwrap();
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|e: &InboxRow| {
                let sender = e.sender.clone();
                Box::pin(async move { format!("ack {sender}") })
            }));
        rt.deliver(&entry).await.unwrap();
        // Give the actor a moment to finish the turn.
        for _ in 0..50 {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            let rows = store.inbox_for("a").unwrap();
            if rows[0].delivery_state == "acked" { break; }
        }
        let rows = store.inbox_for("a").unwrap();
        assert_eq!(rows[0].delivery_state, "acked");
        let transcript = store.transcript("a-s-main").unwrap();
        assert_eq!(transcript.len(), 2,
                   "user delivery + assistant reply");
        assert_eq!(transcript[1].content, "ack user");
        assert_eq!(store.status("a").unwrap(), AgentStatus::Idle);
        rt.shutdown();
    }

    #[tokio::test]
    async fn mcp_tool_schemas_enter_the_model_context() {
        // Mandate 13.2 end to end: an agent with a granted+enabled
        // MCP server gets the server's tool schemas in its system
        // context.  The local HTTP endpoint CAPTURES the request
        // body, so the assertion is about what the model would
        // really see.
        use std::io::{Read as _, Write as _};
        use std::net::TcpListener;

        let capture = std::env::temp_dir().join(format!(
            "las-mcp-note-{}.json", std::process::id()));
        let _ = std::fs::remove_file(&capture);
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let cap = capture.clone();
        std::thread::spawn(move || {
            use std::io::{BufRead as _, BufReader, Write as _};
            if let Ok((stream, _)) = listener.accept() {
                // Line-oriented header reads + an exact body read
                // (the providers.rs mock pattern): robust against
                // the request arriving in several TCP segments.
                let mut reader =
                    BufReader::new(stream.try_clone().unwrap());
                let mut body = String::new();
                let mut content_length = 0usize;
                let mut first = String::new();
                if reader.read_line(&mut first).is_ok() {
                    loop {
                        let mut h = String::new();
                        if reader.read_line(&mut h)
                            .unwrap_or(0) == 0
                            || h.trim().is_empty() { break; }
                        let lower = h.to_lowercase();
                        if let Some(v) = lower.strip_prefix(
                            "content-length:") {
                            content_length =
                                v.trim().parse().unwrap_or(0);
                        }
                    }
                    let mut buf = vec![0u8; content_length];
                    if reader.read_exact(&mut buf).is_ok() {
                        body = String::from_utf8_lossy(
                            &buf).into_owned();
                    }
                }
                let _ = std::fs::write(&cap, body);
                let mut stream = stream;
                let out = br#"{"choices":[{"message":{
                    "content":"noted"}}],
                    "usage":{"prompt_tokens":50,
                    "completion_tokens":2}}"#;
                let _ = stream.write_all(format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: \
                     application/json\r\nContent-Length: {}\r\n\
                     Connection: close\r\n\r\n",
                    out.len()).as_bytes());
                let _ = stream.write_all(out);
            }
        });

        let store = Arc::new(CoreStore::in_memory().unwrap());
        store.create_agent("u", "u", "", &ProfileSpec {
            system_prompt: "You are u.".into(),
            model_provider: "localmock".into(),
            model_id: "m".into(),
            params: serde_json::Value::Null,
            tool_grants: vec!["mcp:docs".to_string()],
        }).unwrap();
        let clients = crate::mcp::McpClients::new(store.clone());
        clients.upsert(&crate::mcp::McpServerConfig {
            id: "docs".into(),
            command: "python3".into(),
            url: None,
            args: vec![
                "tests/fixtures/mcp_echo_server.py".into()],
            env: Default::default(),
            allow_tools: vec!["*".into()],
        }).unwrap();
        clients.set_enabled("docs", true).unwrap();

        let mut adapters =
            crate::providers::AdapterRegistry::new();
        adapters.register("localmock",
            crate::providers::OpenAiCompatAdapter::new(
                &format!("http://{addr}"), None).unwrap());
        let scheduler = Arc::new(
            crate::scheduler::InferenceScheduler::new(4, 2));
        let responder = production_responder(
            store.clone(), scheduler, Arc::new(adapters),
            clients);
        let rt = AgentRuntime::new(store.clone(), responder);
        let entry = store.send_message(
            "user", "u", "what tools do you have?", "",
            BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();

        let body = std::fs::read_to_string(&capture)
            .expect("the request body was captured");
        let parsed: serde_json::Value =
            serde_json::from_str(&body)
            .expect("valid JSON body");
        let system = parsed["messages"][0]["content"]
            .as_str().unwrap_or_default();
        assert!(system.starts_with("You are u."),
                "the agent's own prompt leads: {system}");
        assert!(system.contains("MCP tools available this turn"),
                "{system}");
        assert!(system.contains("mcp:docs:echo"), "{system}");
        assert!(system.contains("UNTRUSTED"), "{system}");
        assert!(system.contains("TOOL_CALL"), "{system}");
        // And the reply made it back through the loop.
        let t = store.transcript("u-s-main").unwrap();
        assert!(t.iter().any(|m| m.content.contains("noted")));
        rt.shutdown();
        let _ = std::fs::remove_file(&capture);
    }

    #[tokio::test]
    async fn provider_usage_is_recorded_with_our_estimate() {
        // Mandate 9.3 end to end: the production responder calls a
        // real (local) HTTP endpoint, and the provider's EXACT
        // token counts land in token_usage next to our pre-call
        // estimate - the input every later budget calibration uses.
        use std::io::{Read as _, Write as _};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 8192];
                let _ = stream.read(&mut buf);
                let body = concat!(
                    r#"{"choices":[{"message":{"content":"ok"}}],"#,
                    r#""usage":{"prompt_tokens":420,"#,
                    r#""completion_tokens":7}}"#);
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: \
                     application/json\r\nContent-Length: {}\r\n\
                     Connection: close\r\n\r\n{}",
                    body.len(), body);
                let _ = stream.write_all(resp.as_bytes());
            }
        });
        let store = Arc::new(CoreStore::in_memory().unwrap());
        store.create_agent("u", "u", "", &ProfileSpec {
            system_prompt: "You are u.".into(),
            model_provider: "localmock".into(),
            model_id: "m".into(),
            params: serde_json::Value::Null,
            tool_grants: vec![],
        }).unwrap();
        let mut adapters = crate::providers::AdapterRegistry::new();
        adapters.register("localmock",
            crate::providers::OpenAiCompatAdapter::new(
                &format!("http://{addr}"), None).unwrap());
        let scheduler = Arc::new(
            crate::scheduler::InferenceScheduler::new(4, 2));
        let responder = production_responder(
            store.clone(), scheduler, Arc::new(adapters),
            crate::mcp::McpClients::new(store.clone()));
        let rt = AgentRuntime::new(store.clone(), responder);
        let entry = store.send_message(
            "user", "u", "count me", "", BusyMode::Wait).unwrap();
        rt.deliver(&entry).await.unwrap();

        let last = store.last_token_usage("u", "localmock", "m")
            .unwrap().expect("usage recorded");
        assert_eq!(last.0, 420, "the provider's exact count");
        assert!(last.1 > 0, "our pre-call estimate: {last:?}");
        // And the reply really came from the endpoint.
        let t = store.transcript("u-s-main").unwrap();
        assert!(t.iter().any(|m| m.content.contains("ok")),
                "the model answered");
        rt.shutdown();
    }

    #[tokio::test]
    async fn rollover_splits_segments_and_continues() {
        // Mandate 9.4: with a tiny per-agent policy the session rolls
        // over into new segments, the logical conversation continues
        // (every turn is answered), and the lineage is inspectable.
        let store = Arc::new(CoreStore::in_memory().unwrap());
        store.create_agent("a", "a", "", &ProfileSpec {
            system_prompt: "You are a.".into(),
            model_provider: "mock".into(), model_id: "m".into(),
            params: serde_json::json!({"rollover_max_messages": 2}),
            tool_grants: vec![],
        }).unwrap();
        let rt = AgentRuntime::new(store.clone(), echo_responder());
        for i in 0..4 {
            let entry = store.send_message(
                "user", "a", &format!("msg {i}"), "k", BusyMode::Wait)
                .unwrap();
            let state = rt.deliver(&entry).await.unwrap();
            assert_eq!(state, DeliveryState::Acked,
                       "every turn is answered: turn {i}");
        }

        let lineage = crate::sessions::session_lineage(&store, "a");
        assert!(lineage.len() >= 3,
                "the tiny policy rolled over twice: {:?}",
                lineage.iter().map(|s| s.id.clone()).collect::<Vec<_>>());
        assert_eq!(lineage[0].id, "a-s-main");
        for (i, seg) in lineage.iter().enumerate() {
            if i + 1 < lineage.len() {
                assert_eq!(seg.status, "rolled_over");
                assert_eq!(lineage[i + 1].segment_of, seg.id,
                           "segments chain via segment_of");
                let prior = store.transcript(&seg.id).unwrap();
                assert!(prior.last().unwrap().content.contains(
                    "rolled over to"), "inspectable closing note");
                let head = store.transcript(&lineage[i + 1].id)
                    .unwrap();
                assert_eq!(head[0].role, "system");
                assert!(head[0].content.contains(
                    "Prior segment"), "summary head");
            } else {
                assert_eq!(seg.status, "open");
            }
        }

        // The logical conversation never lost a turn: all four
        // inbounds and all four replies are somewhere in the lineage.
        let mut inbounds = 0;
        let mut replies = 0;
        for seg in &lineage {
            for m in store.transcript(&seg.id).unwrap() {
                if m.role == "user" && m.content.contains("msg ") {
                    inbounds += 1;
                }
                if m.role == "assistant"
                    && m.content.contains("reply from a") {
                    replies += 1;
                }
            }
        }
        assert_eq!(inbounds, 4, "no turn was dropped by a rollover");
        assert_eq!(replies, 4, "no reply was dropped by a rollover");
        rt.shutdown();
    }

    #[tokio::test]
    async fn wait_mode_blocks_until_ack() {
        let store = store_with_agents();
        let entry = store.send_message(
            "user", "a", "ping", "k2", BusyMode::Wait).unwrap();
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|_: &InboxRow| Box::pin(async {
                "pong".to_string() })));
        let state = rt.deliver(&entry).await.unwrap();
        assert_eq!(state, DeliveryState::Acked);
        rt.shutdown();
    }

    #[tokio::test]
    async fn two_agents_run_independently_same_model() {
        // Mandate 2.2: independent agents share the model
        // infrastructure; state stays independent.
        let store = store_with_agents();
        let ea = store.send_message("user", "a", "for a", "ka",
                                    BusyMode::Wait).unwrap();
        let eb = store.send_message("user", "b", "for b", "kb",
                                    BusyMode::Wait).unwrap();
        let replies = Arc::new(Mutex::new(Vec::<String>::new()));
        let sink = replies.clone();
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(move |e: &InboxRow| {
                let sink = sink.clone();
                let recipient = e.recipient.clone();
                Box::pin(async move {
                    let r = format!("reply-to-{recipient}");
                    sink.lock().unwrap().push(r.clone());
                    r
                })
            }));
        let (ra, rb) = tokio::join!(rt.deliver(&ea), rt.deliver(&eb));
        assert_eq!(ra.unwrap(), DeliveryState::Acked);
        assert_eq!(rb.unwrap(), DeliveryState::Acked);
        // Each transcript is private to its agent.
        let ta = store.transcript("a-s-main").unwrap();
        let tb = store.transcript("b-s-main").unwrap();
        assert_eq!(ta[1].content, "reply-to-a");
        assert_eq!(tb[1].content, "reply-to-b");
        rt.shutdown();
    }

    #[tokio::test]
    async fn restart_recovers_undelivered_entries() {
        let store = store_with_agents();
        let entry = store.send_message("user", "b", "after restart", "kr",
                                       BusyMode::Queue).unwrap();
        // Runtime restarts (no delivery happened yet).
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|_: &InboxRow| Box::pin(async {
                "recovered".to_string() })));
        assert_eq!(rt.recover().await, 1);
        for _ in 0..50 {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            let rows = store.inbox_for("b").unwrap();
            if rows[0].delivery_state == "acked" { break; }
        }
        assert_eq!(store.inbox_for("b").unwrap()[0].delivery_state,
                   "acked", "the entry was delivered after recovery");
        // A second recovery finds nothing pending.
        let rt2 = AgentRuntime::new(store.clone(),
            Arc::new(|_: &InboxRow| Box::pin(async {
                "x".to_string() })));
        assert_eq!(rt2.recover().await, 0);
        rt.shutdown();
        rt2.shutdown();
    }

    #[tokio::test]
    async fn disabled_agent_not_recovered() {
        let store = store_with_agents();
        let entry = store.send_message("user", "b", "x", "kd",
                                       BusyMode::Queue).unwrap();
        store.set_enabled("b", false).unwrap();
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|_: &InboxRow| Box::pin(async {
                "x".to_string() })));
        assert_eq!(rt.recover().await, 0,
                   "disabled agents receive nothing");
        assert_eq!(store.inbox_for("b").unwrap()[0].delivery_state,
                   "accepted");
        let _ = entry;
        rt.shutdown();
    }

    #[tokio::test]
    async fn deletion_survives_runtime() {
        // Store outlives the runtime: persistence is the truth.
        let store = store_with_agents();
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|_: &InboxRow| Box::pin(async {
                "x".to_string() })));
        rt.shutdown();
        store.remove_agent("a", RetentionPolicy::KeepMemory).unwrap();
        assert!(store.get_agent("a").unwrap().is_none());
    }
}
