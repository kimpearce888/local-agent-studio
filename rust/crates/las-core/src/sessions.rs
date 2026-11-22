//! Automatic session rollover (mandate 9.4).
//!
//! One logical conversation is carried across MULTIPLE session
//! segments when the transcript outgrows the configured policy, and
//! the rollover happens BEFORE the context becomes unsafe - never
//! mid-turn.  The schema was designed for this from the start
//! (`agent_sessions.status`, `segment_of`); this module is the
//! lifecycle.
//!
//! Before a rollover the module does exactly what the mandate lists:
//! the original transcript stays durable and untouched
//! (append-only), a STRUCTURED continuity summary is produced, pinned
//! instructions and unresolved requirements are preserved verbatim
//! where possible, important references/decisions are carried over,
//! the next segment is created with `segment_of` pointing back, and
//! both the closing note (old segment) and the head summary (new
//! segment) make the transition inspectable.  The desktop's session
//! combo already lists every segment with its status, so prior
//! segments are user-inspectable, and [`restore_segment`] lets the
//! user continue from an earlier segment.
//!
//! The continuity summary is CODE-EXTRACTED, not model-generated:
//! deterministic, inspectable, and testable without a model server
//! (none exists in this build sandbox).  A model-generated summary
//! would go through the same provider adapters when configured; the
//! extraction here is the always-available floor, not a claim of
//! model-quality summarisation.

use crate::context::estimate_tokens;
use crate::storage::{CoreStore, MessageRow, SessionRow};

/// Rollover when the transcript exceeds EITHER cap.  Defaults keep a
/// long margin below the default 8192-token context budget: the
/// budget drops OLDEST turns to fit, but the logical conversation
/// must not silently lose its early history - rollover preserves it
/// in a prior segment instead.
pub const DEFAULT_MAX_MESSAGES: usize = 200;
pub const DEFAULT_MAX_TOKENS: usize = 24_576;

/// How many recent messages the continuity summary carries verbatim
/// (each capped) so the next segment opens with working context.
const TAIL_MESSAGES: usize = 6;

/// Per-message cap inside the summary tail.
const TAIL_CAP_TOKENS: usize = 120;

/// A single extracted line is capped so one huge turn cannot blow up
/// the summary.
const LINE_CAP_TOKENS: usize = 80;

#[derive(Debug, Clone, PartialEq)]
pub struct RolloverPolicy {
    pub max_messages: usize,
    pub max_tokens: usize,
}

impl Default for RolloverPolicy {
    fn default() -> Self {
        Self {
            max_messages: DEFAULT_MAX_MESSAGES,
            max_tokens: DEFAULT_MAX_TOKENS,
        }
    }
}

impl RolloverPolicy {
    /// Per-agent override through profile params:
    /// `rollover_max_messages`, `rollover_max_tokens`.
    pub fn from_params(params: &serde_json::Value) -> Self {
        let mut p = Self::default();
        if let Some(v) = params.get("rollover_max_messages")
            .and_then(|v| v.as_u64()) {
            p.max_messages = v.max(2) as usize;
        }
        if let Some(v) = params.get("rollover_max_tokens")
            .and_then(|v| v.as_u64()) {
            p.max_tokens = v.max(256) as usize;
        }
        p
    }
}

/// Segments are numbered; the rank orders a lineage even when
/// timestamps tie (rollover happens within one second easily).
/// `-s-main` is rank 0, `-s-seg3` is 3.
pub fn segment_rank(id: &str) -> u64 {
    id.rsplit("-s-seg").next()
        .and_then(|n| n.parse().ok())
        .unwrap_or(0)
}

/// The active segment: the newest `open` session, find-or-create
/// `-s-main` on first delivery.  Crash-safe in both orders: if a
/// rollover was interrupted after creating the new segment, the new
/// segment (higher rank) is still the one picked.
pub fn current_session(store: &CoreStore, agent_id: &str)
                       -> SessionRow {
    if let Ok(sessions) = store.list_sessions(agent_id) {
        if let Some(active) = sessions.iter()
            .filter(|s| s.status == "open")
            .max_by_key(|s| (segment_rank(&s.id), s.created_at.clone()))
        {
            return active.clone();
        }
    }
    let row = SessionRow {
        id: format!("{agent_id}-s-main"),
        agent_id: agent_id.to_string(),
        title: "main".into(),
        status: "open".into(),
        segment_of: String::new(),
        created_at: crate::storage::utc_now(),
    };
    // First delivery ever (or a store wiped of sessions): create.
    // A race with a concurrent find-or-create collapses into the
    // same PK, so the failure mode is benign.
    let _ = store.create_session(&row);
    row
}

/// Does this transcript (INCLUDING the inbound message that is about
/// to be appended) exceed the policy?  Checked before the append so
/// the inbound lands in the NEXT segment, where the conversation
/// continues.
pub fn needs_rollover(messages: &[MessageRow],
                      policy: &RolloverPolicy) -> bool {
    messages.len() > policy.max_messages
        || messages.iter()
            .map(|m| estimate_tokens(&m.content))
            .sum::<usize>() > policy.max_tokens
}

fn cap(text: &str, tokens: usize) -> String {
    match text.char_indices().nth(tokens * 4) {
        Some((idx, _)) => {
            let mut cut = text[..idx].to_string();
            cut.push('…');
            cut
        }
        None => text.to_string(),
    }
}

fn matching_lines(messages: &[MessageRow], roles: &[&str],
                  needles: &[&str]) -> Vec<String> {
    let mut out = Vec::new();
    for m in messages.iter().rev() {
        if !roles.contains(&m.role.as_str()) {
            continue;
        }
        for line in m.content.lines() {
            let t = line.trim();
            if t.is_empty() || t.len() > LINE_CAP_TOKENS * 4 * 4 {
                continue;
            }
            if needles.iter().any(|n| t.to_lowercase()
                                  .contains(n)) {
                let capped = cap(t, LINE_CAP_TOKENS);
                if !out.contains(&capped) {
                    out.push(capped);
                }
            }
        }
    }
    out.reverse();   // oldest first reads naturally
    out
}

/// The structured continuity summary for the next segment.  Every
/// section is labelled; nothing silently disappears (the full prior
/// transcript stays in its segment).
pub fn continuity_summary(prior: &SessionRow,
                          messages: &[MessageRow],
                          pinned: &[String]) -> String {
    let mut s = String::new();
    s.push_str(&format!(
        "Session segment continuation. Prior segment: {} ({} turns) \
         - the logical conversation continues here.\n",
        prior.id, messages.len()));

    // 9.4 step 3: pinned instructions and unresolved requirements,
    // preserved verbatim (capped per line, never paraphrased away).
    let mut pinned_lines: Vec<String> = pinned.iter()
        .map(|p| cap(p, LINE_CAP_TOKENS)).collect();
    pinned_lines.extend(matching_lines(
        messages, &["user", "system", "assistant"], &["PINNED:"]));
    if !pinned_lines.is_empty() {
        s.push_str("\nPINNED (carry these forward exactly):\n");
        for l in pinned_lines {
            s.push_str(&format!("  - {l}\n"));
        }
    }

    let unresolved = matching_lines(
        messages, &["user"], &["?", "todo", "open question",
                               "still need", "unresolved", "waiting"]);
    if !unresolved.is_empty() {
        s.push_str("\nUNRESOLVED (requirements still open):\n");
        for l in unresolved {
            s.push_str(&format!("  - {l}\n"));
        }
    }

    // 9.4 step 4: important references, decisions and artifacts.
    let decisions = matching_lines(
        messages, &["assistant", "user"], &["decision", "decided",
                                            "we will", "agreed"]);
    if !decisions.is_empty() {
        s.push_str("\nDECISIONS:\n");
        for l in decisions {
            s.push_str(&format!("  - {l}\n"));
        }
    }
    let references = matching_lines(
        messages, &["assistant", "user", "tool"], &["workspace/",
                                                    ".md", ".txt",
                                                    ".rs", ".py",
                                                    "file:", "artifact"]);
    if !references.is_empty() {
        s.push_str("\nREFERENCES AND ARTIFACTS:\n");
        for l in references {
            s.push_str(&format!("  - {l}\n"));
        }
    }

    // Working context: the recent tail, verbatim but capped.
    if !messages.is_empty() {
        s.push_str("\nRECENT TURNS (oldest first of the kept tail):\n");
        for m in messages.iter()
            .rev().take(TAIL_MESSAGES).collect::<Vec<_>>()
            .iter().rev() {
            s.push_str(&format!(
                "  [{}] {}\n", m.role, cap(&m.content, TAIL_CAP_TOKENS)));
        }
    }
    s
}

fn next_segment(store: &CoreStore, agent_id: &str) -> u64 {
    store.list_sessions(agent_id)
        .map(|ss| ss.iter().map(|s| segment_rank(&s.id)).max()
             .unwrap_or(0) + 1)
        .unwrap_or(1)
}

fn new_segment_row(agent_id: &str, n: u64, prior_id: &str,
                   title: String) -> SessionRow {
    SessionRow {
        id: format!("{agent_id}-s-seg{n}"),
        agent_id: agent_id.to_string(),
        title,
        status: "open".into(),
        segment_of: prior_id.to_string(),
        created_at: crate::storage::utc_now(),
    }
}

fn pinned_memory(store: &CoreStore, agent_id: &str) -> Vec<String> {
    store.list_memory(agent_id)
        .map(|ms| ms.into_iter()
             .filter(|m| m.category == "pinned")
             .map(|m| m.content).collect())
        .unwrap_or_default()
}

fn append(store: &CoreStore, session: &str, role: &str,
          content: &str) {
    let _ = store.append_message(&MessageRow {
        session_id: session.into(),
        seq: 0,   // allocated by SQL
        role: role.into(),
        content: content.into(),
        reasoning: String::new(),
        tool_json: String::new(),
        model: String::new(),
        ts: crate::storage::utc_now(),
    });
}

/// Perform the rollover (mandate 9.4 steps 1-7).  `messages` is the
/// PRIOR segment's transcript, passed by the caller which just
/// checked [`needs_rollover`] against it.  Order of operations is
/// crash-safe: the new segment (with the summary head) is created
/// and durably populated BEFORE the prior segment is marked
/// `rolled_over`, so an interrupted rollover leaves the newest open
/// segment as the active one and the summary already in place.
pub fn perform_rollover(store: &CoreStore, agent_id: &str,
                        messages: &[MessageRow])
                        -> rusqlite::Result<SessionRow> {
    let prior = current_session(store, agent_id);
    let summary = continuity_summary(
        &prior, messages, &pinned_memory(store, agent_id));
    let n = next_segment(store, agent_id);
    let next = new_segment_row(
        agent_id, n, &prior.id, format!("segment {n}"));

    // 9.4 step 5 + 2: the next segment opens with the structured
    // summary as its head - the conversation continues from it.
    store.create_session(&next)?;
    append(store, &next.id, "system", &summary);

    // 9.4 step 6: the transition is inspectable from the prior
    // segment too.
    append(store, &prior.id, "system", &format!(
        "Session rolled over to {} ({} turns, policy reached).",
        next.id, messages.len()));
    store.update_session_status(&prior.id, "rolled_over")?;
    Ok(next)
}

/// The full lineage, oldest segment first - what the desktop session
/// combo and any audit view should show.
pub fn session_lineage(store: &CoreStore, agent_id: &str)
                       -> Vec<SessionRow> {
    let mut sessions = store.list_sessions(agent_id)
        .unwrap_or_default();
    sessions.sort_by_key(|s| segment_rank(&s.id));
    sessions
}

/// Restore earlier context (mandate 9.4): continue the logical
/// conversation from an EARLIER segment.  History is append-only, so
/// restoring never rewrites the prior segment - it creates a NEW
/// segment seeded with that segment's continuity summary and closes
/// any currently open segment as `closed` (it was not rolled over;
/// the statuses stay honest).
pub fn restore_segment(store: &CoreStore, agent_id: &str,
                       prior_id: &str)
                       -> rusqlite::Result<SessionRow> {
    let sessions = store.list_sessions(agent_id)?;
    let prior = sessions.iter().find(|s| s.id == prior_id)
        .ok_or_else(|| rusqlite::Error::QueryReturnedNoRows)?;
    let prior = prior.clone();
    let messages = store.transcript(&prior.id)?;
    let mut summary = continuity_summary(
        &prior, &messages, &pinned_memory(store, agent_id));
    summary.push_str(&format!(
        "\n(RESTORED: this segment resumes the conversation from \
         {}.)\n", prior.id));

    let n = next_segment(store, agent_id);
    let next = new_segment_row(
        agent_id, n, &prior.id,
        format!("restored from {}", prior.id));
    store.create_session(&next)?;
    append(store, &next.id, "system", &summary);
    for open in sessions.iter().filter(|s| s.status == "open"
                                       && s.id != next.id) {
        store.update_session_status(&open.id, "closed")?;
    }
    Ok(next)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::CoreStore;

    fn msg(role: &str, content: &str) -> MessageRow {
        MessageRow {
            session_id: "s".into(), seq: 0,
            role: role.into(), content: content.into(),
            reasoning: String::new(), tool_json: String::new(),
            model: String::new(), ts: String::new(),
        }
    }

    fn store() -> CoreStore {
        let s = CoreStore::in_memory().unwrap();
        // agent_sessions.agent_id is a FK: the agent row must exist
        // (in production every delivery target is a created agent).
        let spec = crate::agents::ProfileSpec {
            system_prompt: "You are a.".into(),
            model_provider: "mock".into(), model_id: "m".into(),
            params: serde_json::Value::Null, tool_grants: vec![],
        };
        s.create_agent("a", "a", "", &spec).unwrap();
        s
    }

    #[test]
    fn policy_defaults_and_overrides() {
        let p = RolloverPolicy::default();
        assert_eq!(p.max_messages, DEFAULT_MAX_MESSAGES);
        let params = serde_json::json!({
            "rollover_max_messages": 3, "rollover_max_tokens": 1000});
        let p = RolloverPolicy::from_params(&params);
        assert_eq!(p.max_messages, 3);
        assert_eq!(p.max_tokens, 1000);
        // Clamped, never nonsense.
        let p = RolloverPolicy::from_params(
            &serde_json::json!({"rollover_max_messages": 0}));
        assert_eq!(p.max_messages, 2);
    }

    #[test]
    fn rank_orders_segments_numerically() {
        assert_eq!(segment_rank("a-s-main"), 0);
        assert_eq!(segment_rank("a-s-seg1"), 1);
        assert_eq!(segment_rank("a-s-seg10"), 10);
        assert!(segment_rank("a-s-seg10") > segment_rank("a-s-seg9"));
    }

    #[test]
    fn current_session_find_or_creates_main() {
        let store = store();
        let s = current_session(&store, "a");
        assert_eq!(s.id, "a-s-main");
        assert_eq!(s.status, "open");
        // Second call returns the same row, no duplicate.
        assert_eq!(current_session(&store, "a").id, "a-s-main");
        // And the row is really in the database.
        assert!(store.list_sessions("a").unwrap()
                .iter().any(|s| s.id == "a-s-main"));
    }

    #[test]
    fn needs_rollover_by_messages_and_tokens() {
        let policy = RolloverPolicy {
            max_messages: 2, max_tokens: 10_000 };
        assert!(!needs_rollover(
            &[msg("user", "hi")], &policy));
        assert!(needs_rollover(
            &[msg("user", "a"), msg("user", "b"),
              msg("user", "c")], &policy));
        let token_policy = RolloverPolicy {
            max_messages: 100, max_tokens: 10 };
        assert!(needs_rollover(
            &[msg("user", &"x".repeat(200))], &token_policy));
    }

    #[test]
    fn summary_preserves_the_mandate_sections() {
        let messages = vec![
            msg("user", "PINNED: always cite figures exactly."),
            msg("assistant", "Decision: we will use SQLite WAL."),
            msg("user", "Draft the report in workspace/report.md"),
            msg("assistant", "Wrote workspace/report.md with figures."),
            msg("user", "What about the appendix? still need data."),
            msg("assistant", "The appendix is TODO until data lands."),
        ];
        let prior = SessionRow {
            id: "a-s-main".into(), agent_id: "a".into(),
            title: "main".into(), status: "open".into(),
            segment_of: String::new(), created_at: String::new() };
        let s = continuity_summary(
            &prior, &messages,
            &["always-cited style guide".to_string()]);
        assert!(s.contains("Prior segment: a-s-main (6 turns)"));
        assert!(s.contains("always cite figures exactly"),
                "pinned instructions preserved: {s}");
        assert!(s.contains("always-cited style guide"),
                "memory category 'pinned' preserved");
        assert!(s.contains("What about the appendix?"),
                "unresolved requirement preserved");
        assert!(s.contains("we will use SQLite WAL"),
                "decision preserved");
        assert!(s.contains("workspace/report.md"),
                "reference/artifact preserved");
        assert!(s.contains("[user]"), "tail carried");
    }

    #[test]
    fn perform_rollover_marks_prior_and_seeds_next() {
        let store = store();
        let first = current_session(&store, "a");
        for i in 0..3 {
            append(&store, &first.id, "user", &format!("msg {i}"));
        }
        let messages = store.transcript(&first.id).unwrap();
        let next = perform_rollover(&store, "a", &messages).unwrap();
        assert_eq!(next.id, "a-s-seg1");
        assert_eq!(next.segment_of, "a-s-main");

        // Prior: marked, with the closing note as its last message.
        let lineage = session_lineage(&store, "a");
        assert_eq!(lineage.len(), 2);
        assert_eq!(lineage[0].status, "rolled_over");
        assert_eq!(lineage[1].status, "open");
        let prior_msgs = store.transcript("a-s-main").unwrap();
        assert!(prior_msgs.last().unwrap().content.contains(
            "rolled over to a-s-seg1"), "inspectable transition");

        // Next: opens with the summary head.
        let next_msgs = store.transcript("a-s-seg1").unwrap();
        assert_eq!(next_msgs[0].role, "system");
        assert!(next_msgs[0].content.contains(
            "Prior segment: a-s-main"));
        // The active session is now the new segment.
        assert_eq!(current_session(&store, "a").id, "a-s-seg1");
    }

    #[test]
    fn restore_segment_seeds_from_the_earlier_one() {
        let store = store();
        let first = current_session(&store, "a");
        append(&store, &first.id, "user", "topic one");
        let msgs = store.transcript(&first.id).unwrap();
        let second = perform_rollover(&store, "a", &msgs).unwrap();
        append(&store, &second.id, "user", "topic two");

        // Restore from the FIRST segment: history untouched, a new
        // segment seeded from it, and no session left open other
        // than the new one.
        let restored = restore_segment(&store, "a", "a-s-main")
            .unwrap();
        assert_eq!(restored.id, "a-s-seg2");
        assert_eq!(restored.segment_of, "a-s-main");
        assert!(store.transcript("a-s-seg2").unwrap()[0]
                .content.contains("RESTORED"));
        // The prior segment is never REWRITTEN - its two rows are
        // the original turn plus the rollover closing note (9.4
        // step 6), nothing else.
        let prior = store.transcript("a-s-main").unwrap();
        assert_eq!(prior.len(), 2, "prior segment never rewritten");
        assert_eq!(prior[0].content, "topic one");
        assert!(prior[1].content.contains("rolled over to"));
        let lineage = session_lineage(&store, "a");
        assert_eq!(lineage[1].status, "closed",
                  "the open segment is closed, not faked as rolled");
        assert_eq!(lineage[2].status, "open");
        assert_eq!(current_session(&store, "a").id, "a-s-seg2");
    }
}
