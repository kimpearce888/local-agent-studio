//! Token-budgeted context assembly (mandate section 9).
//!
//! A real agent turn must see more than the bare inbound message:
//! the system prompt, the agent's long-term memory and the recent
//! session transcript all belong in the context, newest-first, under
//! a token budget.  There is no tokenizer dependency here on purpose:
//! `estimate_tokens` is a documented ~4 chars/token approximation -
//! the budget is a safety cap, not an exact invoice.

use crate::providers::ChatMessage;

/// Default context budget for a turn (tokens, approximated).
pub const DEFAULT_CONTEXT_BUDGET_TOKENS: usize = 8192;

/// Long-term memory may claim at most this share of the budget; the
/// transcript keeps the rest (the current message always fits).
const MEMORY_SHARE_NUM: usize = 1;
const MEMORY_SHARE_DEN: usize = 4;

/// A single memory entry is capped so one huge note cannot starve
/// the transcript.
const MEMORY_ENTRY_CAP_TOKENS: usize = 256;

/// Approximate token count: ~4 characters per token.
pub fn estimate_tokens(text: &str) -> usize {
    text.len().div_ceil(4)
}

fn cap_tokens(text: &str, cap: usize) -> String {
    match text.char_indices().nth(cap * 4) {
        Some((idx, _)) => {
            let mut cut = text[..idx].to_string();
            cut.push('…');
            cut
        }
        None => text.to_string(),
    }
}

/// Calibrate the context budget against the provider's EXACT
/// token counts (mandate 9.3: accurate counting through the
/// provider when available, explicitly labelled conservative
/// estimates otherwise).
///
/// `last` is the most recent observation for this agent/model:
/// (exact prompt_tokens the provider counted, our chars/4 estimate
/// of that same request).  When the provider counts MORE than we
/// estimated, the raw budget under-counts the true context, so the
/// effective budget shrinks by the observed ratio (keeping less
/// history, safely).  When the provider counts LESS, the raw
/// chars/4 budget is already conservative and stays.  The returned
/// label names the basis - nothing pretends to be exact.
pub fn calibrate(budget: usize, last: Option<(i64, i64)>)
                 -> (usize, String) {
    let Some((exact, estimate)) = last else {
        return (budget, "conservative estimate (~4 chars/token); no \
                         provider token counts observed yet".into());
    };
    if exact <= 0 || estimate <= 0 {
        return (budget,
                format!("conservative estimate (~4 chars/token); \
                         last provider count: {exact} tokens"));
    }
    let ratio = exact as f64 / estimate as f64;
    if ratio > 1.0 {
        let effective = ((budget as f64 / ratio) as usize)
            .max(64);
        (effective, format!(
            "calibrated against the provider's exact count ({exact} \
             vs {estimate} estimated, ratio {ratio:.2}); the budget \
             shrinks to stay safe"))
    } else {
        (budget, format!(
            "conservative estimate (~4 chars/token) confirmed safe: \
             provider counted {exact} where we estimated {estimate}"))
    }
}

/// Assemble the model-turn context under `budget` tokens:
///
/// 1. the system prompt (kept whole, capped by the budget);
/// 2. long-term memory, newest first, within its share;
/// 3. the recent transcript, newest kept but emitted oldest-first
///    so the model reads chronologically;
/// 4. the current inbound message, always present.
///
/// `transcript` must EXCLUDE the current inbound message (the
/// production responder drops the last user row the actor just
/// appended, which IS the inbound message).
pub fn assemble_context(
    system_prompt: &str,
    memories: &[String],
    transcript: &[(String, String)],   // (role, content), oldest first
    inbound: &str,
    budget: usize,
) -> Vec<ChatMessage> {
    let budget = budget.max(64);
    let mut messages = Vec::new();

    // 1. System prompt, whole.
    let sys = cap_tokens(system_prompt, budget / 2);
    let mut used = estimate_tokens(&sys);
    messages.push(ChatMessage::new("system", &sys));

    // 2. Memory block, newest first, within its share.
    let memory_budget = (budget / MEMORY_SHARE_DEN)
        * MEMORY_SHARE_NUM;
    let mut memory_lines: Vec<String> = Vec::new();
    let mut memory_used = 0usize;
    for note in memories.iter().rev() {
        let capped = cap_tokens(note, MEMORY_ENTRY_CAP_TOKENS);
        let cost = estimate_tokens(&capped);
        if memory_used + cost > memory_budget {
            break;   // the oldest notes are the first to drop
        }
        memory_used += cost;
        memory_lines.push(capped);
    }
    if !memory_lines.is_empty() {
        memory_lines.reverse();   // oldest first reads naturally
        let block = format!(
            "Long-term memory (durable notes):\n{}",
            memory_lines.join("\n"));
        used += estimate_tokens(&block);
        messages.push(ChatMessage::new("system", &block));
    }

    // 3. Inbound message, always present (hard floor).
    let inbound = cap_tokens(inbound, budget / 2);
    let inbound_cost = estimate_tokens(&inbound);

    // 4. Transcript, newest kept, emitted oldest-first.
    let mut history: Vec<ChatMessage> = Vec::new();
    let mut history_used = 0usize;
    let remaining = budget.saturating_sub(used + inbound_cost);
    for (role, content) in transcript.iter().rev() {
        let cost = estimate_tokens(content);
        if history_used + cost > remaining {
            break;   // oldest turns are dropped first
        }
        history_used += cost;
        history.push(ChatMessage::new(role, content));
    }
    history.reverse();
    messages.append(&mut history);

    messages.push(ChatMessage::new("user", &inbound));
    messages
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calibrate_labels_every_basis() {
        // No observation: plain conservative estimate.
        let (b, label) = calibrate(8192, None);
        assert_eq!(b, 8192);
        assert!(label.contains("conservative"), "{label}");
        assert!(label.contains("no provider token counts"),
                "{label}");
        // Provider counts MORE than we estimated: the budget must
        // shrink, the label must name the exact count.
        let (b, label) = calibrate(8192, Some((12_288, 8_192)));
        assert!(b < 8192, "shrunk to stay safe: {b}");
        assert!((b as f64 - 8192.0 / 1.5).abs() < 2.0,
                "ratio applied: {b}");
        assert!(label.contains("12288"), "{label}");
        // Provider counts LESS: already safe, budget unchanged,
        // still labelled.
        let (b, label) = calibrate(8192, Some((4_096, 8_192)));
        assert_eq!(b, 8192);
        assert!(label.contains("confirmed safe"), "{label}");
        // Degenerate observation: refuse to divide by zero.
        let (b, label) = calibrate(8192, Some((0, 0)));
        assert_eq!(b, 8192);
        assert!(label.contains("conservative"), "{label}");
    }

    #[test]
    fn empty_context_is_system_plus_inbound() {
        let msgs = assemble_context(
            "You are a test agent.", &[], &[], "hello", 8192);
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].role, "system");
        assert_eq!(msgs[1].role, "user");
        assert_eq!(msgs[1].content, "hello");
    }

    #[test]
    fn budget_drops_oldest_transcript_first() {
        let old = "x".repeat(400);    // ~100 tokens each
        let new = "y".repeat(400);
        let transcript = vec![
            ("user".into(), old.clone()),
            ("assistant".into(), old.clone()),
            ("user".into(), new.clone()),
            ("assistant".into(), new.clone()),
        ];
        // Budget: system(1) + inbound(1) + remaining 248 fits the
        // two newest turns (200) but not a third (300).
        let msgs = assemble_context(
            "sys", &[], &transcript, "in", 250);
        // msgs[0] is the system prompt, the last is the inbound -
        // everything between is transcript history.
        let history: Vec<&str> = msgs[1..msgs.len() - 1].iter()
            .map(|m| m.content.as_str()).collect();
        assert_eq!(history, vec![new.as_str(), new.as_str()],
                   "exactly the two newest turns survive, in order");
        assert!(!history.contains(&old.as_str()),
                "oldest turns are dropped first");
        // Chronological order: newest user turn right before inbound.
        let last_user = msgs.iter().filter(|m| m.role == "user")
            .last().unwrap();
        assert_eq!(last_user.content, "in");
    }

    #[test]
    fn memory_share_is_capped() {
        let notes: Vec<String> = (0..50)
            .map(|i| format!("note {i} ").repeat(20))
            .collect();
        let msgs = assemble_context(
            "sys", &notes, &[], "in", 4096);
        let memory_block = msgs.iter().find(|m| m.role == "system"
            && m.content.starts_with("Long-term memory"));
        let block = memory_block.expect("memory block present");
        // 1/4 of 4096 = 1024 tokens -> at most ~4096 chars + marker
        assert!(block.content.len() < 5000,
                "memory block respects its share");
    }

    #[test]
    fn inbound_always_present_even_when_everything_else_is_huge() {
        let big: Vec<String> = (0..100)
            .map(|_| "z".repeat(400)).collect();
        let transcript: Vec<(String, String)> = big.iter()
            .map(|c| ("user".into(), c.clone())).collect();
        let msgs = assemble_context(
            "sys", &big, &transcript, "the question", 512);
        assert_eq!(msgs.last().unwrap().role, "user");
        assert_eq!(msgs.last().unwrap().content, "the question");
    }
}
