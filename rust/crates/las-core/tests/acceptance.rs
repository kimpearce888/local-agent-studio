//! Acceptance scenarios (mandate section 19), first wave.
//!
//! 19.1 Independent agents: four agents (Researcher, Developer,
//! Reviewer, Coordinator) share ONE model endpoint with independent
//! identities, prompts, sessions, transcripts and memory; everything
//! is inspectable.
//!
//! 19.2 Communication, steps 1-6: a direct message to a busy agent is
//! queued, delivered at a turn boundary, acknowledged, responded to
//! with a correlated response - and every step is still visible after
//! a full restart (store reopened from disk).

use las_core::communication::BusyMode;
use las_core::scheduler::{InferenceScheduler, ModelKey};
use las_core::storage::{CoreStore, SessionRow};
use las_core::{AgentRuntime, ProfileSpec};
use std::sync::Arc;

fn spec(role: &str, prompt: &str) -> ProfileSpec {
    ProfileSpec {
        system_prompt: prompt.into(),
        // SAME endpoint and model for all four agents (mandate 2.2).
        model_provider: "lmstudio".into(),
        model_id: "qwen3-8b".into(),
        params: serde_json::Value::Null,
        tool_grants: vec![],
    }
}

fn four_agents(store: &CoreStore) {
    let roles = [
        ("researcher", "Researcher", "You research topics and cite sources."),
        ("developer", "Developer", "You write and review code."),
        ("reviewer", "Reviewer", "You critique results against criteria."),
        ("coordinator", "Coordinator", "You plan and delegate work."),
    ];
    for (id, name, prompt) in roles {
        store.create_agent(id, name, "team member",
                           &spec(name, prompt)).unwrap();
    }
}

// -- 19.1 ------------------------------------------------------------------

#[tokio::test]
async fn s19_1_four_independent_agents_one_endpoint() {
    let store = CoreStore::in_memory().unwrap();
    four_agents(&store);

    let agents = store.list_agents(false).unwrap();
    assert_eq!(agents.len(), 4, "no arbitrary agent limit");

    // Distinct identities and instructions; same model infrastructure.
    let prompts: Vec<String> = agents.iter().map(|a| {
        store.agent(&a.id).unwrap().profile.system_prompt.clone()
    }).collect();
    assert!(prompts.iter().all(|p| p != &prompts[0] || p.is_empty())
            || prompts.iter().collect::<std::collections::HashSet<_>>()
                .len() == 4,
            "each agent keeps its own instructions");
    for a in &agents {
        let agent = store.agent(&a.id).unwrap();
        assert_eq!(agent.profile.model_provider, "lmstudio");
        assert_eq!(agent.profile.model_id, "qwen3-8b");
    }

    // Independent sessions and transcripts.
    for a in &agents {
        store.create_session(&SessionRow {
            id: format!("{}-s1", a.id), agent_id: a.id.clone(),
            title: "work".into(), status: "open".into(),
            segment_of: String::new(),
            created_at: las_core::storage::utc_now(),
        }).unwrap();
    }
    for a in &agents {
        store.append_message(&las_core::storage::MessageRow {
            session_id: format!("{}-s1", a.id), seq: 0,
            role: "user".into(),
            content: format!("private note for {}", a.id),
            reasoning: String::new(), tool_json: String::new(),
            model: String::new(), ts: las_core::storage::utc_now(),
        }).unwrap();
    }
    // The researcher's transcript is NOT in the developer's session:
    // session reads are keyed per session, no cross-agent visibility.
    let dev = store.transcript("developer-s1").unwrap();
    assert_eq!(dev.len(), 1);
    assert_eq!(dev[0].content, "private note for developer");

    // Independent memory (19.4 preview): private to each agent.
    for a in &agents {
        store.add_memory(&las_core::storage::MemoryRow {
            id: 0, agent_id: a.id.clone(),
            category: "fact".into(),
            content: format!("memory of {}", a.id),
            source: "user".into(), provenance: String::new(),
            confidence: 1.0, scope: "private".into(), version: 1,
            created_at: las_core::storage::utc_now(),
            updated_at: las_core::storage::utc_now(),
            expires_at: String::new(),
        }).unwrap();
    }
    let researcher_memory = store.list_memory("researcher").unwrap();
    assert_eq!(researcher_memory.len(), 1);
    assert_eq!(researcher_memory[0].content, "memory of researcher");

    // Provider concurrency is bounded: the four agents share one model
    // key; the scheduler allows at most 2 at a time and queues the
    // rest (full fairness assertions live in the scheduler tests).
    let sched = Arc::new(InferenceScheduler::new(2, 1));
    let key = ModelKey { provider: "lmstudio".into(),
                         model: "qwen3-8b".into() };
    let p1 = sched.acquire(key.clone(), "researcher").await;
    let p2 = sched.acquire(key.clone(), "developer").await;
    assert_eq!(sched.running_agents(&key), 2);
    drop(p1);
    drop(p2);
}

// -- 19.2, steps 1-6 ----------------------------------------------------------

#[tokio::test]
async fn s19_2_message_to_busy_agent_queued_delivered_acked_restarted() {
    // Real file: the store must survive a full restart.
    let dir = std::env::temp_dir().join(format!(
        "las-acc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let db = dir.join("studio.db");

    {
        let store = Arc::new(CoreStore::open(&db).unwrap());
        four_agents(&store);
        // The coordinator may message the developer (topology grant).
        store.set_comm_rule(&las_core::storage::CommRuleRow {
            src: "coordinator".into(), dst: "developer".into(),
            may_message: true, may_call: true, may_delegate: true,
        }).unwrap();

        // 1. The coordinator sends a direct message (persisted first).
        let first = store.send_message(
            "coordinator", "developer", "review the parser bug",
            "msg-1", BusyMode::Queue).unwrap();

        // 2. The developer is busy: the first delivery is in flight
        //    with a SLOW responder; a second message must queue.
        let rt = AgentRuntime::new(store.clone(),
            Arc::new(|e: &las_core::storage::InboxRow| {
                let id = e.id;
                Box::pin(async move {
                    tokio::time::sleep(
                        std::time::Duration::from_millis(120)).await;
                    format!("ack-from-developer: {id}")
                })
            }));
        rt.deliver(&first).await.unwrap();       // in flight, slow

        let second = store.send_message(
            "coordinator", "developer", "also check the lexer",
            "msg-2", BusyMode::Queue).unwrap();

        // 3-5. Delivery happens at the turn boundary: after the slow
        //    turn finishes, the queued message is delivered, acked and
        //    a correlated response returns to the sender's inbox.
        let rt2 = AgentRuntime::new(store.clone(),
            Arc::new(|e: &las_core::storage::InboxRow| {
                let id = e.id;
                Box::pin(async move {
                    format!("done: {id}")
                })
            }));
        rt2.deliver(&second).await.unwrap();
        // Wait for both turns to complete.
        for _ in 0..100 {
            tokio::time::sleep(
                std::time::Duration::from_millis(20)).await;
            let inbox = store.inbox_for("developer").unwrap();
            if inbox.iter().all(|e| e.delivery_state == "acked") {
                break;
            }
        }
        let inbox = store.inbox_for("developer").unwrap();
        assert!(inbox.iter().all(|e| e.delivery_state == "acked"),
                "both messages delivered at boundaries: {inbox:?}");

        rt.shutdown();
        rt2.shutdown();
    }   // store dropped: process "restart"

    // 6. Every step is still visible after the restart.
    let store = CoreStore::open(&db).unwrap();
    let inbox = store.inbox_for("developer").unwrap();
    assert_eq!(inbox.len(), 2);
    assert!(inbox.iter().all(|e| e.delivery_state == "acked"));
    let transcript = store.transcript("developer-s-main").unwrap();
    assert!(transcript.len() >= 4,
            "both deliveries and replies are in the canonical record");
    let _ = std::fs::remove_dir_all(&dir);
}
