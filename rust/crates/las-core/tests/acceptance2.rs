//! Acceptance scenarios 19.3-19.6 (mandate section 19), second wave.
//!
//! 19.3 Session continuity: growing sessions roll over into segments;
//! both segments stay readable and linked; everything survives a
//! full restart.
//!
//! 19.4 Memory: explicit, source-labelled memory persists across
//! restarts and task boundaries; it can be inspected, updated,
//! deleted - never silently lost.
//!
//! 19.5 Task & workflow: a delegated task produces a durable artifact
//! and reports back to the delegator through its inbox.
//!
//! 19.6 Provider & resource: several agents on two models share
//! bounded, fair, INDEPENDENT inference capacity.

use las_core::communication::{
    BusyMode, DelegationPayload, DeliveryState,
};
use las_core::scheduler::{InferenceScheduler, ModelKey};
use las_core::storage::{
    utc_now, CommRuleRow, CoreStore, MemoryRow, MessageRow,
};
use las_core::{AgentRuntime, ProfileSpec};
use std::sync::Arc;

fn spec(model: &str) -> ProfileSpec {
    ProfileSpec {
        system_prompt: "You are a team member.".into(),
        model_provider: "lmstudio".into(), model_id: model.into(),
        params: serde_json::Value::Null, tool_grants: vec![],
    }
}

fn two_agents(store: &CoreStore) {
    store.create_agent("coordinator", "Coordinator", "", &spec("qwen3-8b"))
        .unwrap();
    store.create_agent("developer", "Developer", "", &spec("qwen3-8b"))
        .unwrap();
}

// -- 19.3 -------------------------------------------------------------------

#[tokio::test]
async fn s19_3_session_rollover_survives_restart() {
    let dir = std::env::temp_dir().join(format!(
        "las-193-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let db = dir.join("studio.db");

    let segment_two = {
        let store = Arc::new(CoreStore::open(&db).unwrap());
        two_agents(&store);
        let rt = AgentRuntime::new(store.clone(), Arc::new(
            |_: &las_core::storage::InboxRow| {
                Box::pin(async { "ok".to_string() })
            }));
        // Segment one: two delivered turns.
        for (i, key) in ["k1", "k2"].iter().enumerate() {
            let e = store.send_message("user", "developer",
                &format!("task {i}"), key, BusyMode::Wait).unwrap();
            rt.deliver(&e).await.unwrap();
        }
        // Rollover: segment two continues the identity.
        let fresh = store.rollover_session("developer",
                                           "developer-s-main",
                                           "after compaction").unwrap();
        // One more turn in the new segment.
        let e = store.send_message("user", "developer",
                                   "task after rollover", "k3",
                                   BusyMode::Wait).unwrap();
        rt.deliver(&e).await.unwrap();
        rt.shutdown();
        fresh
    };   // restart

    let store = CoreStore::open(&db).unwrap();
    // Both segments exist and are linked.
    let sessions = store.list_sessions("developer").unwrap();
    assert_eq!(sessions.len(), 2);
    let rolled = sessions.iter().find(|s| s.status == "rolled_over")
        .expect("old segment kept");
    let current = sessions.iter().find(|s| s.id == segment_two)
        .expect("new segment exists");
    assert_eq!(current.segment_of, rolled.id,
               "the new segment links to its predecessor");
    // The full history reads across both segments, in order.
    let history = store.agent_history("developer").unwrap();
    assert_eq!(history.len(), 6,
               "3 user turns + 3 assistant replies across segments");
    assert!(history.iter().any(|m| m.content.contains(
        "task after rollover")));
    assert!(history.iter().any(|m| m.content.contains("task 0")));
    let _ = std::fs::remove_dir_all(&dir);
}

// -- 19.4 -------------------------------------------------------------------

#[tokio::test]
async fn s19_4_memory_persists_with_provenance_across_restart() {
    let dir = std::env::temp_dir().join(format!(
        "las-194-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let db = dir.join("studio.db");

    {
        let store = CoreStore::open(&db).unwrap();
        two_agents(&store);
        // Explicit user-approved memory with provenance.
        let id = store.add_memory(&MemoryRow {
            id: 0, agent_id: "developer".into(),
            category: "preference".into(),
            content: "Prefers Rust for new tooling".into(),
            source: "user".into(),
            provenance: "chat 2026-11-21, approved by user".into(),
            confidence: 1.0, scope: "private".into(), version: 1,
            created_at: utc_now(), updated_at: utc_now(),
            expires_at: String::new(),
        }).unwrap();
        // An edit is a new version, never a silent overwrite.
        store.update_memory(id, "Prefers Rust 2021 edition",
                            &utc_now()).unwrap();
    }

    let store = CoreStore::open(&db).unwrap();
    let memory = store.list_memory("developer").unwrap();
    assert_eq!(memory.len(), 1);
    let m = &memory[0];
    assert_eq!(m.content, "Prefers Rust 2021 edition");
    assert_eq!(m.version, 2, "edits are versioned");
    assert_eq!(m.source, "user");
    assert!(m.provenance.contains("approved"),
            "provenance is inspectable after restart");
    // Deletion is explicit.
    store.delete_memory(m.id).unwrap();
    assert!(store.list_memory("developer").unwrap().is_empty());
    let _ = std::fs::remove_dir_all(&dir);
}

// -- 19.5 -------------------------------------------------------------------

#[tokio::test]
async fn s19_5_delegation_produces_artifact_and_reports_back() {
    let store = Arc::new(CoreStore::in_memory().unwrap());
    two_agents(&store);
    // Bidirectional topology so the report can return.
    store.set_comm_rule(&CommRuleRow {
        src: "coordinator".into(), dst: "developer".into(),
        may_message: true, may_call: true, may_delegate: true,
    }).unwrap();
    store.set_comm_rule(&CommRuleRow {
        src: "developer".into(), dst: "coordinator".into(),
        may_message: true, may_call: true, may_delegate: true,
    }).unwrap();

    // The "model" of the developer: performs the work, records a
    // durable artifact and reports back through the inbox.
    let work_store = store.clone();
    let rt = AgentRuntime::new(store.clone(), Arc::new(
        move |e: &las_core::storage::InboxRow| {
            let store = work_store.clone();
            let entry = e.clone();
            Box::pin(async move {
                if entry.kind == "delegation" {
                    let payload: DelegationPayload =
                        serde_json::from_str(&entry.payload_json)
                            .unwrap_or(DelegationPayload {
                                objective: String::new(),
                                scope: String::new(),
                                expected_result: String::new(),
                                acceptance_criteria: vec![],
                                context_refs: vec![],
                                artifact_refs: vec![],
                                permission_requests: vec![],
                                resource_limits:
                                    serde_json::Value::Null,
                                deadline_seconds: 0,
                                failure_policy: "report".into(),
                                report_to: String::new(),
                                lineage: vec![],
                                max_depth: 3,
                            });
                    // Durable artifact on the shared blackboard.
                    let artifact = store.add_blackboard_item(
                        "artifact",
                        &format!("deliverable for: {}",
                                 payload.objective),
                        &entry.recipient, &utc_now()).unwrap();
                    // Report back to the delegator's inbox.
                    let _ = store.send_message(
                        &entry.recipient, &entry.sender,
                        &format!("task done, artifact {artifact}"),
                        &format!("report-{}", entry.id),
                        BusyMode::Async);
                    format!("delegated task completed, \
                             artifact {artifact}")
                } else {
                    "ack".to_string()
                }
            })
        }));

    // The coordinator delegates a real task.
    let entry = store.delegate("coordinator", "developer",
        &DelegationPayload {
            objective: "Write the parser tests".into(),
            scope: "tests/ only".into(),
            expected_result: "passing test file".into(),
            acceptance_criteria: vec![
                "covers edge cases".into()],
            context_refs: vec![], artifact_refs: vec![],
            permission_requests: vec![],
            resource_limits: serde_json::Value::Null,
            deadline_seconds: 300,
            failure_policy: "report".into(),
            report_to: "coordinator".into(),
            lineage: vec![], max_depth: 3,
        }).unwrap();
    rt.deliver(&entry).await.unwrap();

    // The chain completed: acked + artifact + report.
    for _ in 0..100 {
        tokio::time::sleep(std::time::Duration::from_millis(20))
            .await;
        let inbox = store.inbox_for("developer").unwrap();
        if inbox[0].delivery_state == "acked" { break; }
    }
    assert_eq!(
        store.inbox_for("developer").unwrap()[0].delivery_state,
        DeliveryState::Acked.as_str());
    let artifacts = store.blackboard().unwrap();
    assert_eq!(artifacts.len(), 1);
    assert_eq!(artifacts[0].1, "artifact");
    assert!(artifacts[0].2.contains("Write the parser tests"));
    let coord_inbox = store.inbox_for("coordinator").unwrap();
    assert_eq!(coord_inbox.len(), 1,
               "the developer reported back through the inbox");
    assert!(coord_inbox[0].payload_json.contains("artifact"));
    rt.shutdown();
}

// -- 19.6 -------------------------------------------------------------------

#[tokio::test]
async fn s19_6_two_models_independent_fair_capacity() {
    let sched = Arc::new(InferenceScheduler::new(2, 1));
    let qwen = ModelKey { provider: "lmstudio".into(),
                          model: "qwen3-8b".into() };
    let llama = ModelKey { provider: "ollama".into(),
                           model: "llama-3.1-8b".into() };

    // Fill BOTH qwen slots; a third qwen request queues...
    let q1 = sched.acquire(qwen.clone(), "researcher").await;
    let q2 = sched.acquire(qwen.clone(), "developer").await;
    let q3task = {
        let s = sched.clone();
        let k = qwen.clone();
        tokio::spawn(async move { s.acquire(k, "reviewer").await })
    };
    tokio::time::sleep(std::time::Duration::from_millis(30)).await;
    assert_eq!(sched.queue_depth(&qwen), 1);

    // ...while the OTHER model serves immediately: independent
    // capacity, no cross-model interference.
    let l1 = sched.acquire(llama.clone(), "coordinator").await;
    assert_eq!(sched.running_agents(&llama), 1);
    assert_eq!(sched.queue_depth(&llama), 0);

    // Fairness within the saturated model: releasing one slot wakes
    // the queued third agent.
    drop(q1);
    let q3 = tokio::time::timeout(
        std::time::Duration::from_millis(300), q3task).await
        .expect("queued request served after release").unwrap();
    assert_eq!(sched.running_agents(&qwen), 2);
    drop(q2); drop(q3); drop(l1);
}

// Keep MessageRow referenced (used by 19.3 history assertions).
#[allow(dead_code)]
fn _m(r: MessageRow) -> MessageRow { r }
