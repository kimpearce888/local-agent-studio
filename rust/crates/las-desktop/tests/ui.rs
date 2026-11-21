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
