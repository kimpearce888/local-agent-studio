//! Agent-to-agent communication as explicit, persisted operations
//! (mandate section 7).
//!
//! Every operation is checked against the communication topology
//! (`comm_rules`), persisted BEFORE delivery (inbox rows with explicit
//! delivery states), deduplicated by idempotency key, and protected
//! against delegation cycles and unbounded depth by lineage tracking.
//! Busy-agent modes are recorded on the inbox row; the runtime
//! interprets them at turn boundaries.

use crate::storage::{utc_now, CommRuleRow, CoreStore, InboxRow};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageKind {
    Message,
    Call,
    Delegation,
    Transfer,
    Channel,
}

impl MessageKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Message => "message",
            Self::Call => "call",
            Self::Delegation => "delegation",
            Self::Transfer => "transfer",
            Self::Channel => "channel",
        }
    }
}

/// Delivery states make uncertainty explicit (mandate 7.1): a message
/// is never "delivered" merely because the sender's API accepted it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DeliveryState {
    /// Persisted by the sender; not yet handed to the recipient.
    Accepted,
    /// The runtime claimed it for a turn boundary.
    Queued,
    /// Currently being handed to the recipient actor.
    InFlight,
    /// The recipient actor received it (not yet processed).
    Delivered,
    /// The recipient acknowledged processing.
    Acked,
    /// Processing finished (response recorded).
    Processed,
    /// Delivery failed; retry policy decides.
    Failed,
    /// Expired before delivery.
    Expired,
    /// The outcome is unknown (e.g. a crash mid-delivery); the
    /// resolution workflow decides, it never silently becomes ok.
    Uncertain,
}

impl DeliveryState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::Queued => "queued",
            Self::InFlight => "in_flight",
            Self::Delivered => "delivered",
            Self::Acked => "acked",
            Self::Processed => "processed",
            Self::Failed => "failed",
            Self::Expired => "expired",
            Self::Uncertain => "uncertain",
        }
    }
}

/// Busy-agent delivery modes (mandate 7.2).  The sender (user or
/// agent) chooses one; the recipient's runtime enforces it at a safe
/// point - a model call is never edited mid-flight.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum BusyMode {
    /// Deliver at the next turn boundary (default, safest).
    Queue,
    /// Inject at a supported safe point inside the active turn.
    Steer,
    /// Stop the active work, then deliver.
    Interrupt,
    /// Sender blocks until acknowledged.
    Wait,
    /// Sender continues immediately; delivery proceeds in parallel.
    Async,
}

impl BusyMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Queue => "queue",
            Self::Steer => "steer",
            Self::Interrupt => "interrupt",
            Self::Wait => "wait",
            Self::Async => "async",
        }
    }
    pub fn parse(s: &str) -> Self {
        match s {
            "steer" => Self::Steer,
            "interrupt" => Self::Interrupt,
            "wait" => Self::Wait,
            "async" => Self::Async,
            _ => Self::Queue,
        }
    }
}

pub const DEFAULT_MAX_DELEGATION_DEPTH: usize = 3;

/// A delegated task's contract (mandate 7.4).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DelegationPayload {
    pub objective: String,
    pub scope: String,
    pub expected_result: String,
    pub acceptance_criteria: Vec<String>,
    #[serde(default)]
    pub context_refs: Vec<String>,
    #[serde(default)]
    pub artifact_refs: Vec<String>,
    #[serde(default)]
    pub permission_requests: Vec<String>,
    #[serde(default)]
    pub resource_limits: serde_json::Value,
    #[serde(default)]
    pub deadline_seconds: u64,
    #[serde(default = "default_failure_policy")]
    pub failure_policy: String,
    #[serde(default)]
    pub report_to: String,
    /// Chain of agent ids from the original delegator (cycle/depth
    /// protection, mandate 7.8).
    #[serde(default)]
    pub lineage: Vec<String>,
    #[serde(default = "default_depth")]
    pub max_depth: usize,
}

fn default_failure_policy() -> String { "report".into() }
fn default_depth() -> usize { DEFAULT_MAX_DELEGATION_DEPTH }

/// Control transfer record (mandate 7.5).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TransferPayload {
    pub task_ref: String,
    pub previous_owner: String,
    pub new_owner: String,
    pub reason: String,
    pub state_snapshot: serde_json::Value,
}

#[derive(Debug)]
pub enum CommError {
    UnknownAgent(String),
    Denied { src: String, dst: String, op: &'static str },
    CycleDelegation(String),
    DepthExceeded { depth: usize, max: usize },
    NotAMember { agent: String, channel: String },
    Storage(rusqlite::Error),
}

impl std::fmt::Display for CommError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownAgent(id) => write!(f, "unknown agent {id}"),
            Self::Denied { src, dst, op } => write!(
                f, "topology denies {op} from {src} to {dst}"),
            Self::CycleDelegation(id) => write!(
                f, "delegation cycle: {id} is already in the lineage"),
            Self::DepthExceeded { depth, max } => write!(
                f, "delegation depth {depth} exceeds the limit {max}"),
            Self::NotAMember { agent, channel } => write!(
                f, "{agent} is not a member of {channel}"),
            Self::Storage(e) => write!(f, "storage error: {e}"),
        }
    }
}

type Result<T> = std::result::Result<T, CommError>;

fn map(e: rusqlite::Error) -> CommError { CommError::Storage(e) }

fn require_agent(s: &CoreStore, id: &str) -> Result<()> {
    if id != "user" && s.get_agent(id).map_err(map)?.is_none() {
        return Err(CommError::UnknownAgent(id.into()));
    }
    Ok(())
}

fn rule_allows(rule: &CommRuleRow, op: &str) -> bool {
    match op {
        "message" => rule.may_message,
        "call" => rule.may_call,
        "delegate" => rule.may_delegate,
        "transfer" => rule.may_message,   // transfer requires a message path
        _ => false,
    }
}

fn enforce(s: &CoreStore, src: &str, dst: &str,
           op: &'static str) -> Result<CommRuleRow> {
    require_agent(s, src)?;
    require_agent(s, dst)?;
    let rule = s.comm_rule(src, dst).map_err(map)?;
    if !rule_allows(&rule, op) {
        return Err(CommError::Denied { src: src.into(), dst: dst.into(),
                                      op });
    }
    Ok(rule)
}

impl CoreStore {
    /// Direct message (mandate 7.1): persisted before delivery, with
    /// an idempotency key so retries are safe.
    pub fn send_message(&self, src: &str, dst: &str, content: &str,
                        idempotency_key: &str, mode: BusyMode)
                        -> Result<InboxRow> {
        enforce(self, src, dst, "message")?;
        let payload = serde_json::json!({ "content": content });
        self.enqueue(&InboxRow {
            id: 0, recipient: dst.into(), sender: src.into(),
            kind: MessageKind::Message.as_str().into(),
            payload_json: payload.to_string(),
            delivery_state: DeliveryState::Accepted.as_str().into(),
            idempotency_key: idempotency_key.into(),
            busy_mode: mode.as_str().into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        }).map_err(map)
    }

    /// Ask / request-response (mandate 7.3).  The correlation id links
    /// the call to its response; timeouts and cancellation are runtime
    /// concerns - a call never blocks indefinitely by construction.
    pub fn agent_call(&self, src: &str, dst: &str, question: &str,
                      correlation_id: &str, timeout_seconds: u64)
                      -> Result<InboxRow> {
        enforce(self, src, dst, "call")?;
        let payload = serde_json::json!({
            "question": question,
            "correlation_id": correlation_id,
            "timeout_seconds": timeout_seconds,
        });
        self.enqueue(&InboxRow {
            id: 0, recipient: dst.into(), sender: src.into(),
            kind: MessageKind::Call.as_str().into(),
            payload_json: payload.to_string(),
            delivery_state: DeliveryState::Accepted.as_str().into(),
            idempotency_key: correlation_id.into(),
            busy_mode: BusyMode::Queue.as_str().into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        }).map_err(map)
    }

    /// Record a call's response (correlated).
    pub fn call_response(&self, correlation_id: &str, responder: &str,
                         content: &str) -> Result<InboxRow> {
        // The response goes back to the caller's inbox as a message
        // that references the correlation id.
        let original = self.find_by_idem(responder, correlation_id)?;
        let caller = original.map(|o| o.sender).unwrap_or_else(
            || "user".into());
        let payload = serde_json::json!({
            "content": content, "correlation_id": correlation_id,
            "kind": "call_response",
        });
        self.enqueue(&InboxRow {
            id: 0, recipient: caller, sender: responder.into(),
            kind: MessageKind::Message.as_str().into(),
            payload_json: payload.to_string(),
            delivery_state: DeliveryState::Delivered.as_str().into(),
            idempotency_key: format!("{correlation_id}-resp"),
            busy_mode: BusyMode::Async.as_str().into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: utc_now(), acked_at: String::new(),
            expires_at: String::new(),
        }).map_err(map)
    }

    fn find_by_idem(&self, recipient: &str, key: &str)
                    -> Result<Option<InboxRow>> {
        use rusqlite::OptionalExtension;
        let conn = self.raw_conn();
        conn.query_row(
            "SELECT * FROM inbox WHERE recipient=? AND idempotency_key=?",
            rusqlite::params![recipient, key], |r| {
                Ok(InboxRow {
                    id: r.get("id")?, recipient: r.get("recipient")?,
                    sender: r.get("sender")?, kind: r.get("kind")?,
                    payload_json: r.get("payload_json")?,
                    delivery_state: r.get("delivery_state")?,
                    idempotency_key: r.get("idempotency_key")?,
                    busy_mode: r.get("busy_mode")?,
                    attempts: r.get("attempts")?,
                    created_at: r.get("created_at")?,
                    delivered_at: r.get("delivered_at")?,
                    acked_at: r.get("acked_at")?,
                    expires_at: r.get("expires_at")?,
                })
            }).optional().map_err(map)
    }

    /// Delegate a task to an existing agent (mandate 7.4).  Cycles and
    /// excessive depth are refused before anything is persisted.
    pub fn delegate(&self, src: &str, dst: &str,
                    payload: &DelegationPayload) -> Result<InboxRow> {
        enforce(self, src, dst, "delegate")?;
        if payload.lineage.contains(&dst.to_string()) {
            return Err(CommError::CycleDelegation(dst.into()));
        }
        let depth = payload.lineage.len() + 1;
        if depth > payload.max_depth {
            return Err(CommError::DepthExceeded {
                depth, max: payload.max_depth });
        }
        let mut full = payload.clone();
        if !full.lineage.contains(&src.to_string()) {
            full.lineage.push(src.into());
        }
        self.enqueue(&InboxRow {
            id: 0, recipient: dst.into(), sender: src.into(),
            kind: MessageKind::Delegation.as_str().into(),
            payload_json: serde_json::to_string(&full)
                .unwrap_or_else(|_| "{}".into()),
            delivery_state: DeliveryState::Accepted.as_str().into(),
            idempotency_key: String::new(),
            busy_mode: BusyMode::Queue.as_str().into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        }).map_err(map)
    }

    /// Transfer control of a task to another agent (mandate 7.5).
    /// Different from a message: it moves responsibility, records the
    /// previous owner and snapshots the current state.
    pub fn transfer_control(&self, src: &str,
                            payload: &TransferPayload) -> Result<InboxRow> {
        enforce(self, src, &payload.new_owner, "transfer")?;
        self.enqueue(&InboxRow {
            id: 0, recipient: payload.new_owner.clone(),
            sender: src.into(),
            kind: MessageKind::Transfer.as_str().into(),
            payload_json: serde_json::to_string(payload)
                .unwrap_or_else(|_| "{}".into()),
            delivery_state: DeliveryState::Accepted.as_str().into(),
            idempotency_key: String::new(),
            busy_mode: BusyMode::Interrupt.as_str().into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        }).map_err(map)
    }

    /// Post to a channel (mandate 7.6): membership is required, and
    /// members without a message grant cannot post.
    pub fn post_channel(&self, channel_id: &str, author: &str,
                        content: &str, reply_to: i64) -> Result<i64> {
        require_agent(self, author)?;
        let members = self.channel_members(channel_id).map_err(map)?;
        if !members.iter().any(|m| m == author) {
            return Err(CommError::NotAMember {
                agent: author.into(), channel: channel_id.into() });
        }
        // Membership plus a message path to the channel is required:
        // each member must be reachable by the author (topology is
        // enforced at runtime, not just displayed - mandate section 8).
        for member in &members {
            if member == author { continue; }
            let rule = self.comm_rule(author, member).map_err(map)?;
            if !rule.may_message {
                return Err(CommError::Denied {
                    src: author.into(), dst: member.clone(),
                    op: "channel" });
            }
        }
        let id = self.post_to_channel(channel_id, author, content,
                                      reply_to, &utc_now()).map_err(map)?;
        // Fan out to every member's inbox as a channel entry.
        for member in members {
            if member == author { continue; }
            let _ = self.enqueue(&InboxRow {
                id: 0, recipient: member.clone(), sender: author.into(),
                kind: MessageKind::Channel.as_str().into(),
                payload_json: serde_json::json!({
                    "channel": channel_id, "post_id": id,
                    "content": content }).to_string(),
                delivery_state: DeliveryState::Accepted.as_str().into(),
                idempotency_key: format!("chp-{id}-{member}"),
                busy_mode: BusyMode::Queue.as_str().into(),
                attempts: 0, created_at: utc_now(),
                delivered_at: String::new(), acked_at: String::new(),
                expires_at: String::new(),
            });
        }
        Ok(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::ProfileSpec;

    fn spec() -> ProfileSpec {
        ProfileSpec {
            system_prompt: "p".into(),
            model_provider: "lmstudio".into(), model_id: "m".into(),
            params: serde_json::Value::Null, tool_grants: vec![],
        }
    }

    fn setup() -> CoreStore {
        let s = CoreStore::in_memory().unwrap();
        for id in ["a", "b", "c"] {
            s.create_agent(id, id, "", &spec()).unwrap();
        }
        s
    }

    #[test]
    fn message_persists_before_delivery_with_states() {
        let s = setup();
        let row = s.send_message("user", "a", "hello", "k1",
                                 BusyMode::Queue).unwrap();
        assert_eq!(row.delivery_state, "accepted");
        assert_eq!(row.busy_mode, "queue");
        s.update_delivery(row.id, "delivered", &utc_now()).unwrap();
        let stored = &s.inbox_for("a").unwrap()[0];
        assert_eq!(stored.delivery_state, "delivered");
        assert!(!stored.delivered_at.is_empty());
    }

    #[test]
    fn idempotent_retry_returns_same_row() {
        let s = setup();
        let first = s.send_message("user", "a", "m", "op-7",
                                   BusyMode::Queue).unwrap();
        let again = s.send_message("user", "a", "m", "op-7",
                                   BusyMode::Queue).unwrap();
        assert_eq!(first.id, again.id);
        assert_eq!(s.inbox_for("a").unwrap().len(), 1);
    }

    #[test]
    fn topology_denies_ungranted_agent_messages() {
        let s = setup();
        let err = s.send_message("a", "b", "hi", "", BusyMode::Queue)
            .unwrap_err();
        assert!(matches!(err, CommError::Denied { .. }));
        s.set_comm_rule(&CommRuleRow {
            src: "a".into(), dst: "b".into(), may_message: true,
            may_call: false, may_delegate: false,
        }).unwrap();
        assert!(s.send_message("a", "b", "hi", "", BusyMode::Queue).is_ok());
        // The message grant does not imply call or delegate.
        assert!(matches!(
            s.agent_call("a", "b", "q", "c1", 30).unwrap_err(),
            CommError::Denied { .. }));
        assert!(matches!(
            s.delegate("a", "b", &DelegationPayload {
                objective: "o".into(), scope: String::new(),
                expected_result: String::new(),
                acceptance_criteria: vec![], context_refs: vec![],
                artifact_refs: vec![], permission_requests: vec![],
                resource_limits: serde_json::Value::Null,
                deadline_seconds: 0, failure_policy: "report".into(),
                report_to: String::new(), lineage: vec![],
                max_depth: 3,
            }).unwrap_err(),
            CommError::Denied { .. }));
    }

    #[test]
    fn unknown_recipient_refused() {
        let s = setup();
        assert!(matches!(
            s.send_message("user", "zzz", "x", "", BusyMode::Queue)
                .unwrap_err(),
            CommError::UnknownAgent(_)));
    }

    #[test]
    fn delegation_cycles_and_depth_refused() {
        let s = setup();
        s.set_comm_rule(&CommRuleRow {
            src: "a".into(), dst: "b".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        s.set_comm_rule(&CommRuleRow {
            src: "b".into(), dst: "c".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        s.set_comm_rule(&CommRuleRow {
            src: "c".into(), dst: "a".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        let mk = |lineage: Vec<&str>| DelegationPayload {
            objective: "o".into(), scope: String::new(),
            expected_result: String::new(), acceptance_criteria: vec![],
            context_refs: vec![], artifact_refs: vec![],
            permission_requests: vec![],
            resource_limits: serde_json::Value::Null,
            deadline_seconds: 0, failure_policy: "report".into(),
            report_to: String::new(),
            lineage: lineage.into_iter().map(String::from).collect(),
            max_depth: 3,
        };
        // a→b→c then c tries to delegate back to a: a is in the lineage.
        let err = s.delegate("c", "a", &mk(vec!["a", "b"])).unwrap_err();
        assert!(matches!(err, CommError::CycleDelegation(_)));
        // Depth 4 > max 3 refused (d is outside the lineage, so only
        // the depth rule fires).
        s.create_agent("d", "d", "", &spec()).unwrap();
        s.set_comm_rule(&CommRuleRow {
            src: "c".into(), dst: "d".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        let err = s.delegate("c", "d", &DelegationPayload {
            max_depth: 3, ..mk(vec!["a", "b", "c"])
        }).unwrap_err();
        assert!(matches!(err, CommError::DepthExceeded { .. }));
        // A legal chain works and the lineage grows.
        let ok = s.delegate("a", "b", &mk(vec![])).unwrap();
        let stored: DelegationPayload = serde_json::from_str(
            &ok.payload_json).unwrap();
        assert_eq!(stored.lineage, vec!["a".to_string()]);
    }

    #[test]
    fn transfer_records_ownership_chain() {
        let s = setup();
        s.set_comm_rule(&CommRuleRow {
            src: "a".into(), dst: "b".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        let row = s.transfer_control("a", &TransferPayload {
            task_ref: "task-9".into(), previous_owner: "a".into(),
            new_owner: "b".into(), reason: "better fit".into(),
            state_snapshot: serde_json::json!({"progress": 0.4}),
        }).unwrap();
        assert_eq!(row.kind, "transfer");
        assert_eq!(row.busy_mode, "interrupt");
        let payload: TransferPayload =
            serde_json::from_str(&row.payload_json).unwrap();
        assert_eq!(payload.previous_owner, "a");
        assert_eq!(payload.state_snapshot["progress"], 0.4);
    }

    #[test]
    fn channel_membership_and_fanout() {
        let s = setup();
        s.create_channel("ch", "general", "").unwrap();
        // b is NOT a member: posting is refused.
        assert!(matches!(
            s.post_channel("ch", "b", "hi", 0).unwrap_err(),
            CommError::NotAMember { .. }));
        s.join_channel("ch", "a").unwrap();
        s.join_channel("ch", "b").unwrap();
        s.set_comm_rule(&CommRuleRow {
            src: "a".into(), dst: "b".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        let post = s.post_channel("ch", "a", "hello team", 0).unwrap();
        assert!(post > 0);
        assert_eq!(s.channel_posts("ch").unwrap().len(), 1);
        // b (member) has a channel entry in its inbox; a does not get
        // its own post back.
        let b_inbox = s.inbox_for("b").unwrap();
        assert_eq!(b_inbox.len(), 1);
        assert_eq!(b_inbox[0].kind, "channel");
        assert!(s.inbox_for("a").unwrap().is_empty());
    }

    #[test]
    fn call_response_correlates() {
        let s = setup();
        s.set_comm_rule(&CommRuleRow {
            src: "a".into(), dst: "b".into(), may_message: true,
            may_call: true, may_delegate: true,
        }).unwrap();
        s.agent_call("a", "b", "what time is it?", "corr-1", 30).unwrap();
        let resp = s.call_response("corr-1", "b", "12:00").unwrap();
        assert_eq!(resp.recipient, "a");
        let payload: serde_json::Value =
            serde_json::from_str(&resp.payload_json).unwrap();
        assert_eq!(payload["correlation_id"], "corr-1");
        assert_eq!(payload["content"], "12:00");
    }
}
