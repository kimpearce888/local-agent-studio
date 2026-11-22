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

