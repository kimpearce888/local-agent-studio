//! The Local Agent Studio desktop shell (ADR-002: egui/eframe).
//!
//! A thin view layer over `las-core`: the shell owns no domain logic.
//! Navigation follows the mandate's fourteen areas.  Areas whose
//! engine subsystems are still being built render an honest
//! "under construction" panel that names the milestone - never fake
//! controls (mandate section 14).
//!
//! The same `draw_shell` code runs on screen (main.rs) and headlessly
//! in tests (egui_kittest, ADR-003).

use las_core::storage::{CoreStore, InboxRow};
use std::sync::Arc;

mod bootstrap;
pub use bootstrap::RuntimeHandle;

mod screens;
pub use screens::{channels_screen, history_screen,
                  providers_screen, sessions_screen,
                  teams_screen, tools_screen,
                  projects_screen, artifacts_screen, tasks_screen,
                  workflow_screen, settings_screen};

/// The mandate's fourteen navigation areas (section 14).
/// The tool vocabulary agents can be granted explicitly (mandate
/// 10.1). Unticked = not granted; there is no implicit grant.
pub const KNOWN_TOOLS: [&str; 4] = [
    "read_file", "write_file", "list_files", "web_fetch",
];

pub const NAV_AREAS: [&str; 14] = [
    "Dashboard", "Projects", "Agents", "Agent Teams / Network",
    "Inbox & Messages", "Collaboration Channels", "Tasks & Delegations",
    "Workflow Studio", "Sessions & Memory", "Providers & Models",
    "Tools & Integrations", "Activity & History", "Artifacts & Files",
    "Settings & Diagnostics",
];

#[derive(Debug, Clone, PartialEq)]
pub enum NavArea {
    Dashboard,
    Agents,
    Inbox,
    Sessions,
    Channels,
    Teams,
    Providers,
    Projects,
    Artifacts,
    Tasks,
    Workflow,
    Settings,
    History,
    Tools,
    Planned(usize),     // index into NAV_AREAS
}

pub struct AppState {
    pub store: Arc<CoreStore>,
    pub nav: NavArea,
    /// Inbox screen: selected recipient agent.
    pub inbox_selected: String,
    /// New-agent form fields (real state, not demo data).
    pub new_agent_name: String,
    pub new_agent_prompt: String,
    pub new_agent_tools: Vec<String>,
    pub new_agent_model: String,
    /// Message composer fields.
    pub compose_to: String,
    pub compose_text: String,
    pub status_line: String,
    // Sessions & Memory screen.
    pub sessions_selected_agent: String,
    pub sessions_selected_session: String,
    pub memory_category: String,
    pub memory_content: String,
    // Channels screen.
    pub channel_new_id: String,
    pub channel_new_name: String,
    pub channel_selected: String,
    pub channel_join_agent: String,
    pub channel_post_author: String,
    pub channel_post_content: String,
    // Providers & Models screen.
    pub endpoint_name: String,
    pub endpoint_url: String,
    pub endpoint_key_env: String,
    pub provider_models: std::sync::Arc<
        std::sync::Mutex<Vec<String>>>,
    pub provider_note: std::sync::Arc<std::sync::Mutex<String>>,
    pub probe_log: std::sync::Arc<
        std::sync::Mutex<Vec<String>>>,
    pub runtime: Option<RuntimeHandle>,
    // Activity & History screen.
    pub history_selected_run: String,
    // Teams / topology screen.
    /// Artifacts screen buffers.
    pub artifacts_agent: String,
    /// The selected REGISTERED artifact (its logical id).
    pub artifacts_selected_artifact: String,
    pub artifacts_selected: String,
    /// Tasks screen buffers.
    pub task_title: String,
    pub task_instructions: String,
    pub task_to: String,
    /// Workflow screen buffers.
    pub plan_title: String,
    pub plan_goal: String,
    pub plan_selected: String,
    pub plan_step_title: String,
    pub plan_step_instructions: String,
    pub step_edit_plan: String,
    pub plan_exported: String,
    /// Projects screen buffers.
    pub project_name: String,
    pub project_desc: String,
    pub project_agent_input: String,
    pub rule_src: String,
    pub rule_dst: String,
    pub rule_message: bool,
    pub rule_call: bool,
    pub rule_delegate: bool,
}

impl AppState {
    pub fn from_store(store: Arc<CoreStore>) -> Self {
        Self {
            store, nav: NavArea::Dashboard,
            inbox_selected: String::new(),
            new_agent_name: String::new(),
            new_agent_prompt: String::new(),
            new_agent_tools: Vec::new(),
            new_agent_model: "qwen3-8b".into(),
            compose_to: String::new(), compose_text: String::new(),
            status_line: String::new(),
            sessions_selected_agent: String::new(),
            sessions_selected_session: String::new(),
            memory_category: String::new(),
            memory_content: String::new(),
            channel_new_id: String::new(),
            channel_new_name: String::new(),
            channel_selected: String::new(),
            channel_join_agent: String::new(),
            channel_post_author: String::new(),
            channel_post_content: String::new(),
            endpoint_name: String::new(),
            endpoint_url: String::new(),
            endpoint_key_env: String::new(),
            provider_models: std::sync::Arc::new(
                std::sync::Mutex::new(Vec::new())),
            provider_note: std::sync::Arc::new(
                std::sync::Mutex::new(String::new())),
            probe_log: std::sync::Arc::new(
                std::sync::Mutex::new(Vec::new())),
            runtime: None,
            history_selected_run: String::new(),
            artifacts_agent: String::new(),
            artifacts_selected_artifact: String::new(),
            artifacts_selected: String::new(),
            task_title: String::new(),
            task_instructions: String::new(),
            task_to: String::new(),
            plan_title: String::new(),
            plan_goal: String::new(),
            plan_selected: String::new(),
            plan_step_title: String::new(),
            plan_step_instructions: String::new(),
            step_edit_plan: String::new(),
            plan_exported: String::new(),
            project_name: String::new(),
            project_desc: String::new(),
            project_agent_input: String::new(),
            rule_src: String::new(),
            rule_dst: String::new(),
            rule_message: true,
            rule_call: false,
            rule_delegate: false,
        }
    }
}

pub struct ShellApp {
    pub state: AppState,
    frames: u64,
}

impl eframe::App for ShellApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        draw_shell(ui, &mut self.state);
        if let Ok(limit) = std::env::var("LAS_SMOKE_FRAMES") {
            self.frames += 1;
            if let Ok(n) = limit.parse::<u64>() {
                if self.frames >= n {
                    ui.ctx().send_viewport_cmd(
                        egui::ViewportCommand::Close);
                }
            }
        }
    }
}

impl ShellApp {
    pub fn new(state: AppState) -> Self {
        Self { state, frames: 0 }
    }
}

/// Draw the whole shell: side navigation + the current screen.
/// Works inside any `egui::Ui` (the eframe root ui, or a test ui).
pub fn draw_shell(ui: &mut egui::Ui, state: &mut AppState) {
    egui::Panel::left("nav")
        .show(ui, |ui| {
            ui.add_space(8.0);
            ui.heading("Local Agent Studio");
            ui.add_space(8.0);
            for (i, area) in NAV_AREAS.iter().enumerate() {
                let selected = match (&state.nav, *area) {
                    (NavArea::Dashboard, "Dashboard")
                    | (NavArea::Agents, "Agents")
                    | (NavArea::Inbox, "Inbox & Messages") => true,
                    (NavArea::Planned(j), _) => *j == i,
                    _ => false,
                };
                if ui.selectable_label(selected, *area).clicked() {
                    state.nav = match *area {
                        "Dashboard" => NavArea::Dashboard,
                        "Agents" => NavArea::Agents,
                        "Inbox & Messages" => NavArea::Inbox,
                        _ => NavArea::Planned(i),
                    };
                }
            }
        });
    egui::CentralPanel::default().show(ui, |ui| {
        match state.nav.clone() {
            NavArea::Dashboard => dashboard_screen(ui, state),
            NavArea::Agents => agents_screen(ui, state),
            NavArea::Inbox => inbox_screen(ui, state),
            NavArea::Sessions => sessions_screen(ui, state),
            NavArea::Channels => channels_screen(ui, state),
            NavArea::Teams => teams_screen(ui, state),
            NavArea::Providers => providers_screen(ui, state),
            NavArea::History => history_screen(ui, state),
            NavArea::Tools => tools_screen(ui, state),
            NavArea::Projects => projects_screen(ui, state),
            NavArea::Artifacts => artifacts_screen(ui, state),
            NavArea::Tasks => tasks_screen(ui, state),
            NavArea::Workflow => workflow_screen(ui, state),
            NavArea::Settings => settings_screen(ui, state),
            NavArea::Planned(i) => planned_screen(ui, NAV_AREAS[i]),
        }
        ui.add_space(6.0);
        ui.separator();
        ui.label(egui::RichText::new(&state.status_line).weak());
    });
}

fn dashboard_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Dashboard");
    let agents = state.store.list_agents(false)
        .unwrap_or_default();
    let enabled = agents.iter().filter(|a| a.enabled).count();
    let working = agents.iter()
        .filter(|a| a.status == "working").count();
    let mut pending = 0usize;
    let mut acked = 0usize;
    for a in &agents {
        for e in state.store.inbox_for(&a.id).unwrap_or_default() {
            match e.delivery_state.as_str() {
                "acked" | "processed" => acked += 1,
                _ => pending += 1,
            }
        }
    }
    egui::Grid::new("dash").num_columns(2).show(ui, |ui| {
        ui.label("Registered agents"); ui.label(
            agents.len().to_string()); ui.end_row();
        ui.label("Enabled / active"); ui.label(format!(
            "{enabled} / {working} working")); ui.end_row();
        ui.label("Inbox pending delivery"); ui.label(
            pending.to_string()); ui.end_row();
        ui.label("Inbox acknowledged"); ui.label(
            acked.to_string()); ui.end_row();
    });
}

fn agents_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Agents");
    let agents = state.store.list_agents(true)
        .unwrap_or_default();
    egui::ScrollArea::vertical().show(ui, |ui| {
        egui::Grid::new("agents").striped(true).num_columns(6)
            .show(ui, |ui| {
                ui.strong("Name"); ui.strong("Status");
                ui.strong("Model"); ui.strong("Enabled");
                ui.strong("Profile"); ui.strong("Actions");
                ui.end_row();
                for a in &agents {
                    ui.label(&a.name);
                    ui.label(&a.status);
                    let model = state.store.agent(&a.id)
                        .map(|ag| format!("{} / {}",
                            ag.profile.model_provider,
                            ag.profile.model_id))
                        .unwrap_or_default();
                    ui.label(model);
                    ui.label(if a.enabled { "yes" } else { "no" });
                    ui.label(format!(
                        "v{}", a.current_profile_version));
                    ui.horizontal(|ui| {
                        if ui.button(format!("toggle-{}", a.name))
                            .clicked() {
                            let store = state.store.clone();
                            let id = a.id.clone();
                            let enabled = a.enabled;
                            let _ = store.set_enabled(&id, !enabled);
                            state.status_line = format!(
                                "{id} {}", if enabled {
                                    "disabled" } else { "enabled" });
                        }
                        if ui.button(format!("archive-{}", a.name))
                            .clicked() {
                            let _ = state.store.archive_agent(&a.id);
                            state.status_line =
                                format!("{} archived", a.id);
                        }
                    });
                    ui.end_row();
                }
            });
    });
    ui.separator();
    ui.heading("New agent");
    egui::Grid::new("new-agent").num_columns(2).show(ui, |ui| {
        ui.label("Name");
        ui.add(egui::TextEdit::singleline(&mut state.new_agent_name)
            .hint_text("Agent name"));
        ui.end_row();
        ui.label("System prompt");
        ui.add(egui::TextEdit::multiline(&mut state.new_agent_prompt)
            .hint_text("System prompt"));
        ui.end_row();
        ui.label("Model");
        ui.add(egui::TextEdit::singleline(&mut state.new_agent_model)
            .hint_text("Model id"));
        ui.end_row();
    });
    // Explicit tool grants (mandate 10.1/19.7): nothing is granted
    // implicitly; only ticked tools land in the profile.
    ui.label("Tool grants (nothing is granted implicitly):");
    ui.horizontal(|ui| {
        for tool in crate::KNOWN_TOOLS {
            // A real binding the checkbox mutates: reading it after
            // the call reflects the click (a `&mut { granted }` temp
            // would leave the old value in `granted`).
            let mut granted = state.new_agent_tools.iter()
                .any(|t| t == tool);
            if ui.checkbox(&mut granted,
                           format!("grant-{tool}")).changed() {
                if granted {
                    state.new_agent_tools.push(tool.to_string());
                } else {
                    state.new_agent_tools.retain(
                        |t| t != tool);
                }
            }
        }
    });
    if ui.button("Create agent").clicked() {
        let name = state.new_agent_name.trim().to_string();
        if name.is_empty() || state.new_agent_prompt.trim().is_empty() {
            state.status_line =
                "A name and a system prompt are required".into();
        } else {
            let id = name.to_lowercase()
                .replace(|c: char| !c.is_ascii_alphanumeric(), "-");
            let spec = las_core::ProfileSpec {
                system_prompt: state.new_agent_prompt.trim().into(),
                model_provider: "lmstudio".into(),
                model_id: state.new_agent_model.trim().into(),
                params: serde_json::Value::Null,
                tool_grants: state.new_agent_tools.clone(),
            };
            match state.store.create_agent(&id, &name, "team member",
                                           &spec) {
                Ok(_) => {
                    state.status_line =
                        format!("Agent {name} created");
                    state.new_agent_name.clear();
                    state.new_agent_prompt.clear();
                }
                Err(e) => state.status_line = e.to_string(),
            }
        }
    }
}

fn inbox_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Inbox & Messages");
    let agents = state.store.list_agents(false)
        .unwrap_or_default();
    let names: Vec<String> = agents.iter().map(|a| a.id.clone())
        .collect();
    if state.inbox_selected.is_empty() {
        state.inbox_selected = names.first().cloned()
            .unwrap_or_default();
    }
    egui::ComboBox::from_label("Agent inbox")
        .selected_text(&state.inbox_selected)
        .show_ui(ui, |ui| {
            for n in &names {
                if ui.selectable_label(
                    *n == state.inbox_selected, n).clicked() {
                    state.inbox_selected = n.clone();
                }
            }
        });
    ui.add_space(6.0);
    let selected = state.inbox_selected.clone();
    let entries: Vec<InboxRow> = if selected.is_empty() {
        Vec::new()
    } else {
        state.store.inbox_for(&selected).unwrap_or_default()
    };
    egui::ScrollArea::vertical().show(ui, |ui| {
        egui::Grid::new("inbox").striped(true).num_columns(4)
            .show(ui, |ui| {
                ui.strong("From"); ui.strong("Kind");
                ui.strong("Delivery"); ui.strong("Payload");
                ui.end_row();
                for e in &entries {
                    ui.label(&e.sender);
                    ui.label(&e.kind);
                    ui.label(&e.delivery_state);
                    ui.label(payload_summary(&e.payload_json));
                    ui.end_row();
                }
                if entries.is_empty() {
                    ui.label("(no messages)");
                    ui.end_row();
                }
            });
    });
    ui.separator();
    ui.heading("Send a message");
    egui::Grid::new("compose").num_columns(2).show(ui, |ui| {
        ui.label("To");
        ui.add(egui::TextEdit::singleline(&mut state.compose_to)
            .hint_text("Recipient agent id"));
        ui.end_row();
        ui.label("Message");
        ui.add(egui::TextEdit::multiline(&mut state.compose_text)
            .hint_text("Message text"));
        ui.end_row();
    });
    if ui.button("Send message").clicked() {
        let to = state.compose_to.trim().to_string();
        let text = state.compose_text.trim().to_string();
        if to.is_empty() || text.is_empty() {
            state.status_line = "To and message are required".into();
        } else {
            use las_core::communication::BusyMode;
            match state.store.send_message(
                "user", &to, &text, "", BusyMode::Queue) {
                Ok(row) => {
                    state.status_line = format!(
                        "Message {} to {to} accepted",
                        row.id);
                    state.compose_text.clear();
                }
                Err(e) => state.status_line = e.to_string(),
            }
        }
    }
}

fn planned_screen(ui: &mut egui::Ui, area: &str) {
    ui.heading(area);
    ui.label(
        "This area is under construction in the current build; its \
         engine subsystem is on the las-core roadmap (see \
         BUILD_STATUS.md). No placeholder controls are shown.");
}

fn payload_summary(payload: &str) -> String {
    serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|v| v.get("content").and_then(|c| c.as_str())
            .map(str::to_string))
        .unwrap_or_else(|| payload.chars().take(60).collect())
}
