//! Headless UI verification (ADR-003): the same draw code that runs
//! on screen is rendered through egui_kittest's CPU renderer.  `get`
//! asserts presence; clicks perform real store actions.

use egui_kittest::kittest::{by, Queryable};
use egui_kittest::Harness;
use las_core::communication::BusyMode;
use las_core::storage::CoreStore;
use las_core::ProfileSpec;
use las_desktop::{draw_shell, AppState, NavArea, NAV_AREAS};
use std::sync::Arc;

fn store_with_team() -> Arc<CoreStore> {
    let s = Arc::new(CoreStore::in_memory().unwrap());
    for (id, name) in [("researcher", "Researcher"),
                       ("developer", "Developer")] {
        let _ = s.create_agent(id, name, "", &ProfileSpec {
            system_prompt: format!("You are {name}."),
            model_provider: "lmstudio".into(),
            model_id: "qwen3-8b".into(),
            params: serde_json::Value::Null,
            tool_grants: vec![],
        });
    }
    s
}

fn harness_with(state: AppState) -> Harness<'static, AppState> {
    Harness::builder().build_ui_state(
        |ui, state| draw_shell(ui, state), state)
}

#[test]
fn dashboard_counts_real_store_state() {
    let store = store_with_team();
    store.send_message("user", "researcher", "hello", "k1",
                       BusyMode::Queue).unwrap();
    let harness = harness_with(AppState::from_store(store));
    // .get panics when the label is absent - these ARE the
    // assertions: 2 agents registered, 1 inbox entry pending.
    let mut h = harness;
    h.run();
    h.get_all(by().label("Registered agents")).next().unwrap();
    h.get_all(by().label("2")).next().unwrap();
    h.get_all(by().label("Inbox pending delivery")).next().unwrap();
    h.get_all(by().label("1")).next().unwrap();
}

#[test]
fn agents_screen_lists_team_and_toggles_enabled() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Agents,
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("Researcher")).next().unwrap();
    h.get_all(by().label("Developer")).next().unwrap();
    // A REAL action: clicking toggle-Researcher flips the store row.
    h.get_all(by().label("toggle-Researcher")).next().unwrap().click();
    h.run();
    let row = store.get_agent("researcher").unwrap().unwrap();
    assert!(!row.enabled, "the click disabled the agent");
}

#[test]
fn agents_screen_creates_agent_via_form() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Agents,
        new_agent_name: "Reviewer".into(),
        new_agent_prompt: "You critique results.".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("Create agent")).next().unwrap().click();
    h.run();
    assert!(store.get_agent("reviewer").unwrap().is_some(),
            "the form created a real agent");
}

#[test]
fn inbox_screen_shows_entries_and_composer_writes() {
    let store = store_with_team();
    store.send_message("user", "researcher", "first task", "k1",
                       BusyMode::Queue).unwrap();
    let mut h = harness_with(AppState {
        nav: NavArea::Inbox,
        inbox_selected: "researcher".into(),
        compose_to: "developer".into(),
        compose_text: "please review".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    // The inbox row renders with its delivery state.
    h.get_all(by().label("accepted")).next().unwrap();
    // A REAL action: the composer persists a message.
    h.get_all(by().label("Send message")).next().unwrap().click();
    h.run();
    let dev_inbox = store.inbox_for("developer").unwrap();
    assert_eq!(dev_inbox.len(), 1,
               "the composer persisted a message to the developer");
}

#[test]
fn navigation_covers_all_fourteen_areas() {
    let mut h = harness_with(AppState::from_store(store_with_team()));
    h.run();
    for area in NAV_AREAS {
        h.get_all(by().label(area)).next().unwrap();
    }
    // Clicking an unbuilt area shows the honest construction note.
    // (The nav button matches both the button node and its inner text
    // node - take the first match.)
    h.get_all(by().label("Workflow Studio")).next().unwrap().click();
    h.run();
    h.get_all(by().label_contains("under construction")).next().unwrap();
}

// -- Sessions & Memory, Channels, Teams screens --------------------------

#[test]
fn sessions_screen_shows_transcript_and_adds_memory() {
    use las_core::storage::{MessageRow, SessionRow};
    let store = store_with_team();
    // A session with a transcript for the researcher.
    store.create_session(&SessionRow {
        id: "researcher-s1".into(), agent_id: "researcher".into(),
        title: "work".into(), status: "open".into(),
        segment_of: String::new(),
        created_at: las_core::storage::utc_now(),
    }).unwrap();
    store.append_message(&MessageRow {
        session_id: "researcher-s1".into(), seq: 0,
        role: "user".into(), content: "find the bug".into(),
        reasoning: String::new(), tool_json: String::new(),
        model: String::new(), ts: las_core::storage::utc_now(),
    }).unwrap();

    let mut h = harness_with(AppState {
        nav: NavArea::Sessions,
        sessions_selected_agent: "researcher".into(),
        sessions_selected_session: "researcher-s1".into(),
        memory_category: "fact".into(),
        memory_content: "parser bug is in lexer".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("find the bug")).next().unwrap();
    // REAL action: adding memory through the form persists it.
    h.get_all(by().label("Add memory")).next().unwrap().click();
    h.run();
    let memory = store.list_memory("researcher").unwrap();
    assert_eq!(memory.len(), 1);
    assert_eq!(memory[0].content, "parser bug is in lexer");
    assert_eq!(memory[0].source, "user");
    // Delete it via the row button - also a real action.
    let delete_label = format!("delete-memory-{}", memory[0].id);
    h.get_all(by().label(&delete_label)).next().unwrap().click();
    h.run();
    assert!(store.list_memory("researcher").unwrap().is_empty());
}

#[test]
fn channels_screen_creates_joins_and_posts() {
    let store = store_with_team();
    // Topology: researcher may message developer (needed for post
    // fan-out).
    store.set_comm_rule(&las_core::storage::CommRuleRow {
        src: "researcher".into(), dst: "developer".into(),
        may_message: true, may_call: true, may_delegate: true,
    }).unwrap();

    let mut h = harness_with(AppState {
        nav: NavArea::Channels,
        channel_new_id: "general".into(),
        channel_new_name: "General".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    // Create the channel for real.
    h.get_all(by().label("Create channel")).next().unwrap().click();
    h.run();
    assert_eq!(store.list_channels().unwrap().len(), 1);

    // Join with the researcher, then post as the researcher.
    h.get_all(by().label("Join channel")).next().unwrap().click();
    h.run();
    assert_eq!(store.channel_members("general").unwrap(),
               vec!["researcher".to_string()]);

    // Set the post author + content in place, then click Post.
    h.state_mut().channel_post_author = "researcher".into();
    h.state_mut().channel_post_content = "standup at noon".into();
    h.get_all(by().label("Post to channel")).next().unwrap().click();
    h.run();
    let posts = store.channel_posts("general").unwrap();
    assert_eq!(posts.len(), 1);
    assert_eq!(posts[0].1, "researcher");
    // Fan-out delivered a channel entry to the developer member?  No:
    // only the researcher is a member so far; join the developer too.
    store.join_channel("general", "developer").unwrap();
    // The post already happened; a NEW post fans out to both.
    h.state_mut().channel_post_content = "second post".into();
    h.get_all(by().label("Post to channel")).next().unwrap().click();
    h.run();
    let dev_inbox = store.inbox_for("developer").unwrap();
    assert_eq!(dev_inbox.len(), 1,
               "channel fan-out reached the developer member");
    assert_eq!(dev_inbox[0].kind, "channel");
}

#[test]
fn teams_screen_grants_topology_rule() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Teams,
        rule_src: "researcher".into(),
        rule_dst: "developer".into(),
        rule_message: true,
        rule_call: true,
        rule_delegate: false,
        ..AppState::from_store(store.clone())
    });
    h.run();
    // Before saving: the empty-rules hint states the default policy.
    h.get_all(by().label_contains("default deny")).next().unwrap();
    h.get_all(by().label("Save rule")).next().unwrap().click();
    h.run();
    let rule = store.comm_rule("researcher", "developer").unwrap();
    assert!(rule.may_message && rule.may_call);
    assert!(!rule.may_delegate, "the form wrote the exact grant");
    // After saving, the rule is visible in the table.
    h.get_all(by().label("researcher")).next().unwrap();
}

#[test]
fn dashboard_updates_after_agent_created_via_form() {
    // Cross-screen coherence: a new agent appears on the dashboard.
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Agents,
        new_agent_name: "Reviewer".into(),
        new_agent_prompt: "You critique results.".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("Create agent")).next().unwrap().click();
    h.run();
    h.state_mut().nav = NavArea::Dashboard;
    h.run();
    h.get_all(by().label("3")).next().unwrap();
}


// -- 19.7: permission surfaces in the UI drive real enforcement ----

#[test]
fn s19_7_revoking_a_rule_restores_default_deny() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Teams,
        rule_src: "researcher".into(),
        rule_dst: "developer".into(),
        rule_message: true, rule_call: true, rule_delegate: true,
        ..AppState::from_store(store.clone())
    });
    h.run();
    // Grant through the form...
    h.get_all(by().label("Save rule")).next().unwrap().click();
    h.run();
    let granted = store.comm_rule("researcher", "developer").unwrap();
    assert!(granted.may_message && granted.may_call);
    // ...then revoke through the row button: default-deny returns.
    h.get_all(by().label("revoke-researcher->developer"))
        .next().unwrap().click();
    h.run();
    let revoked = store.comm_rule("researcher", "developer").unwrap();
    assert!(!revoked.may_message && !revoked.may_call
            && !revoked.may_delegate);
    // The engine ACTUALLY enforces the revocation.
    let refused = store.send_message(
        "researcher", "developer", "let me in", "k-197",
        las_core::communication::BusyMode::Queue);
    assert!(refused.is_err(),
            "revoked topology must refuse agent->agent messages");
}

#[test]
fn s19_7_tool_grants_are_explicit_and_persisted() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Agents,
        new_agent_name: "Scribe".into(),
        new_agent_prompt: "You take notes.".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    // Nothing granted implicitly: tick exactly one tool.
    h.get_all(by().label("grant-write_file")).next().unwrap().click();
    h.run();
    h.get_all(by().label("Create agent")).next().unwrap().click();
    h.run();
    let agent = store.agent("scribe").unwrap();
    let grants: Vec<String> = serde_json::from_str(
        &agent.profile.tool_grants_json).unwrap();
    assert_eq!(grants, vec!["write_file".to_string()],
               "only the explicitly ticked tool is granted");
}

// -- 19.8: the full desktop walk-through with REAL model turns ------

/// A stoppable loopback OpenAI-compatible server for the desktop
/// walk-through (same contract as the las-core providers tests).
struct TestServer {
    base: String,
    running: std::sync::Arc<std::sync::atomic::AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.running.store(
            false, std::sync::atomic::Ordering::SeqCst);
        if let Some(h) = self.handle.take() { let _ = h.join(); }
    }
}

fn start_test_server() -> TestServer {
    use std::io::{BufRead, BufReader, Read, Write};
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap();
    listener.set_nonblocking(true).unwrap();
    let addr = listener.local_addr().unwrap();
    let running = std::sync::Arc::new(
        std::sync::atomic::AtomicBool::new(true));
    let flag = running.clone();
    let chat_body = r#"{"choices":[{"message":{"content":
        "Hello from the model"}}],"usage":{"prompt_tokens":9,
        "completion_tokens":4}}"#.to_string();
    let models_body = r#"{"data":[{"id":"qwen3-8b"}]}"#.to_string();
    let handle = std::thread::spawn(move || {
        loop {
            if !flag.load(std::sync::atomic::Ordering::SeqCst) {
                break;
            }
            match listener.accept() {
                Ok((mut stream, _)) => {
                    let mut reader = BufReader::new(
                        stream.try_clone().unwrap());
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 {
                        continue;
                    }
                    let parts: Vec<&str> =
                        line.split_whitespace().collect();
                    if parts.len() < 2 { continue; }
                    let path = parts[1].to_string();
                    let mut length = 0usize;
                    loop {
                        let mut h = String::new();
                        if reader.read_line(&mut h).unwrap_or(0) == 0
                            || h.trim().is_empty() { break; }
                        if let Some(v) = h.to_lowercase()
                            .strip_prefix("content-length:") {
                            length = v.trim().parse().unwrap_or(0);
                        }
                    }
                    let mut body = vec![0u8; length];
                    if length > 0 {
                        let _ = reader.read_exact(&mut body);
                    }
                    let payload = match path.as_str() {
                        "/v1/models" => models_body.as_str(),
                        _ => chat_body.as_str(),
                    };
                    let resp = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: \
                         application/json\r\nContent-Length: {}\r\n\
                         Connection: close\r\n\r\n{payload}",
                        payload.len());
                    let _ = stream.write_all(resp.as_bytes());
                    let _ = stream.flush();
                }
                Err(e) if e.kind()
                    == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(
                        std::time::Duration::from_millis(5));
                }
                Err(_) => break,
            }
        }
    });
    TestServer { base: format!("http://{addr}"), running,
                 handle: Some(handle) }
}

#[test]
fn s19_8_full_desktop_walkthrough_with_real_model_turn() {
    let server = start_test_server();
    let store = store_with_team();

    // 1. Register the endpoint through the UI form.
    let mut h = harness_with(AppState {
        nav: NavArea::Providers,
        endpoint_name: "lmstudio".into(),   // matches agent profiles
        endpoint_url: server.base.clone(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("Save endpoint")).next().unwrap().click();
    h.run();
    assert_eq!(store.list_endpoints().unwrap().len(), 1);

    // 2. Start the runtime through the UI.
    h.get_all(by().label("Start runtime")).next().unwrap().click();
    h.run();
    let runtime = h.state_mut().runtime.as_ref().map(|r|
        r.is_running()).unwrap_or(false);
    assert!(runtime, "the runtime is running");

    // 3. Send a message through the composer.
    h.state_mut().nav = NavArea::Inbox;
    h.state_mut().compose_to = "researcher".into();
    h.state_mut().compose_text = "hello model".into();
    h.run();
    h.get_all(by().label("Send message")).next().unwrap().click();
    h.run();

    // 4. The runtime delivers it through the fair scheduler and the
    //    REAL HTTP round-trip: poll the store for the acked turn.
    let mut acked_with_reply = false;
    for _ in 0..200 {
        std::thread::sleep(std::time::Duration::from_millis(50));
        if let Ok(inbox) = store.inbox_for("researcher") {
            if let Some(entry) = inbox.first() {
                if entry.delivery_state == "acked" {
                    let history = store.agent_history("researcher")
                        .unwrap_or_default();
                    if history.iter().any(|m| m.content.contains(
                        "Hello from the model")) {
                        acked_with_reply = true;
                        break;
                    }
                }
            }
        }
    }
    assert!(acked_with_reply,
            "the real model turn completed: queued -> delivered -> \
             acked -> replied through HTTP");

    // 5. The reply is VISIBLE in the UI (Sessions transcript).
    h.state_mut().nav = NavArea::Sessions;
    h.state_mut().sessions_selected_agent = "researcher".into();
    h.state_mut().sessions_selected_session =
        "researcher-s-main".into();
    h.run();
    h.get_all(by().label_contains("Hello from the model"))
        .next().expect("the model reply is on screen");

    // 6. The activity view shows the completed delivery.
    h.state_mut().nav = NavArea::History;
    h.run();
    h.get_all(by().label("acked")).next()
        .expect("the acked delivery is in the activity log");

    // 7. Clean shutdown through the UI.
    h.state_mut().nav = NavArea::Providers;
    h.run();
    h.get_all(by().label("Stop runtime")).next().unwrap().click();
    h.run();
    assert!(h.state_mut().runtime.is_none());
    drop(h);
    drop(server);
}

#[test]
fn tools_screen_shows_vocabulary_and_grant_matrix() {
    let store = store_with_team();
    // researcher holds one explicit tool grant.
    store.create_agent("scribe", "Scribe", "", &ProfileSpec {
        system_prompt: "You take notes.".into(),
        model_provider: "lmstudio".into(),
        model_id: "qwen3-8b".into(),
        params: serde_json::Value::Null,
        tool_grants: vec!["write_file".into()],
    }).unwrap();
    let mut h = harness_with(AppState {
        nav: NavArea::Tools,
        ..AppState::from_store(store)
    });
    h.run();
    h.get_all(by().label("web_fetch")).next()
        .expect("vocabulary lists web_fetch");
    h.get_all(by().label("always")).next()
        .expect("the grant matrix shows the explicit grant \
                 (write_file for scribe)");
    h.get_all(by().label_contains("Pending approvals")).next()
        .expect("the approval gate section is on the screen");
}

#[test]
fn projects_screen_creates_and_staffs_a_project() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Projects,
        project_name: "Launch Prep".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    // A REAL action: clicking Create persists the project.
    h.get_all(by().label("Create")).next().unwrap().click();
    h.run();
    // The open header shows the member list (0 members so far).
    h.get_all(by().label("members (0): (none)")).next()
        .expect("the new project is listed, open, empty");
    // Add a member through the real store path the button drives.
    store.add_project_member("launch-prep", "researcher").unwrap();
    h.run();
    h.get_all(by().label("members (1): researcher")).next()
        .expect("the member shows in the project");
}

#[test]
fn artifacts_screen_browses_real_workspace_files() {
    let dir = std::env::temp_dir().join(format!(
        "las-artifacts-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let store = Arc::new(
        CoreStore::open(dir.join("t.db")).unwrap());
    let _ = store.create_agent("researcher", "Researcher", "",
        &ProfileSpec {
            system_prompt: "You research.".into(),
            model_provider: "mock".into(),
            model_id: "m".into(),
            params: serde_json::Value::Null,
            tool_grants: vec![],
        });
    // A REAL file in the agent's workspace jail.
    let ws = dir.join("workspaces").join("researcher")
        .join("notes");
    std::fs::create_dir_all(&ws).unwrap();
    std::fs::write(ws.join("plan.md"), "step 1: verify")
        .unwrap();
    let mut h = harness_with(AppState {
        nav: NavArea::Artifacts,
        artifacts_agent: "researcher".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("notes/plan.md")).next().unwrap();
    // Select it: the preview shows the real content.
    h.get_all(by().label("notes/plan.md")).next().unwrap()
        .click();
    h.run();
    h.get_all(by().label_contains("step 1: verify")).next()
        .expect("the preview shows the file content");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn tasks_screen_creates_task_and_delivers_to_inbox() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Tasks,
        task_title: "Summarize the paper".into(),
        task_instructions: "One page, key findings.".into(),
        task_to: "researcher".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    // REAL action: the click persists the task AND posts to the
    // agent's real inbox (the runtime will deliver it).
    h.get_all(by().label("Create task")).next().unwrap().click();
    h.run();
    let tasks = store.list_tasks().unwrap();
    assert_eq!(tasks.len(), 1);
    assert_eq!(tasks[0].1, "Summarize the paper");
    assert_eq!(tasks[0].4, "researcher");
    assert_eq!(tasks[0].5, "open");
    let inbox = store.inbox_for("researcher").unwrap();
    assert!(inbox.iter().any(|m| m.payload_json.contains(
        "Summarize the paper")),
        "the task was delivered to the inbox");
    // Mark done via the row button - also a real action.
    let id = tasks[0].0;
    h.get_all(by().label(&format!("done-{id}"))).next().unwrap()
        .click();
    h.run();
    assert_eq!(store.list_tasks().unwrap()[0].5, "done");
}

#[test]
fn workflow_screen_builds_and_exports_schema_v1_plan() {
    let dir = std::env::temp_dir().join(format!(
        "las-wf-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let store = Arc::new(
        CoreStore::open(dir.join("t.db")).unwrap());
    let mut h = harness_with(AppState {
        nav: NavArea::Workflow,
        plan_title: "Launch Review".into(),
        plan_goal: "Review the launch checklist.".into(),
        ..AppState::from_store(store.clone())
    });
    h.run();
    h.get_all(by().label("Create plan")).next().unwrap().click();
    h.run();
    h.get_all(by().label("add-step-launch-review")).next()
        .unwrap().click();
    h.run();
    // Add a step through the real store path the form drives, then
    // run one STABILIZING frame: clicks are coordinate-based, so
    // the tree we click in must match the frame that consumes the
    // click (the added step shifts the layout by one line).
    store.add_plan_step("launch-review", "Check dates",
                        "Verify the launch date.").unwrap();
    h.run();
    // Export: a REAL schema v1 file lands on disk.
    h.get_all(by().label("export-launch-review")).next().unwrap()
        .click();
    h.run();
    h.get_all(by().label_contains("Exported:")).next()
        .expect("the screen reports the export path");
    let file = dir.join("plans").join("launch-review.json");
    let plan: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&file).unwrap()).unwrap();
    assert_eq!(plan["schema_version"], 1);
    assert_eq!(plan["title"], "Launch Review");
    assert_eq!(plan["tasks"][0]["id"], "t1");
    assert_eq!(plan["tasks"][0]["title"], "Check dates");
    assert_eq!(plan["tasks"][0]["needs"], serde_json::json!([]));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn settings_screen_reports_real_diagnostics() {
    let store = store_with_team();
    let mut h = harness_with(AppState {
        nav: NavArea::Settings,
        ..AppState::from_store(store)
    });
    h.run();
    h.get_all(by().label("Desktop version")).next().unwrap();
    h.get_all(by().label("0.4.0")).next()
        .expect("the shipped version is displayed");
    h.get_all(by().label_contains("quick_check: ok")).next()
        .expect("the integrity check runs and reports ok");
    h.get_all(by().label("2")).next()
        .expect("agent count comes from the real store");
}

#[test]
fn tools_screen_shows_modes_and_resolves_approvals() {
    let store = store_with_team();
    // The researcher holds web_fetch behind ask_once.
    store.set_tool_grants("researcher",
        r#"[{"tool":"web_fetch","mode":"ask_once"}]"#).unwrap();
    // A model turn recorded an approval request (the runtime path
    // from the las-core gate tests).
    store.request_tool_approval(
        "researcher", "web_fetch",
        r#"{"url":"https://example.com/x"}"#).unwrap();
    let mut h = harness_with(AppState {
        nav: NavArea::Tools,
        ..AppState::from_store(store.clone())
    });
    h.run();
    // The grant matrix shows the ask-once mode.
    h.get_all(by().label("ask once")).next()
        .expect("the matrix shows the grant mode");
    // The pending request is listed.
    h.get_all(by().label_contains("example.com")).next()
        .expect("the pending request is listed with its args");
    // REAL action: approving clears the queue and upgrades the
    // grant to always (ask-once semantics).
    // REAL action: approving clears the queue.
    h.get_all(by().label("approve-1")).next().unwrap().click();
    h.run();
    assert!(store.pending_approvals().unwrap().is_empty());
    let profile = store.agent("researcher").unwrap().profile;
    assert!(profile.tool_grants_json
        .contains(r#""mode":"always""#),
        "approve upgraded the grant: {}",
        profile.tool_grants_json);
    h.get_all(by().label("always")).next()
        .expect("the matrix reflects the upgrade");

    // Cycling a mode cell really persists: developer's read_file
    // goes off -> always on the first click.
    h.get_all(by().label("cycle-developer-read_file")).next()
        .unwrap().click();
    h.run();
    let p2 = store.agent("developer").unwrap().profile;
    assert!(p2.tool_grants_json
        .contains(r#""tool":"read_file""#),
        "the cycle click persisted a grant: {}", p2.tool_grants_json);
}
