//! Live screens beyond the first three: Sessions & Memory (19.3/19.4
//! surfaces), Collaboration Channels (7.6) and the Teams / Network
//! topology editor (section 8).  Every control performs a real
//! las-core action.

use crate::AppState;
use las_core::storage::{CommRuleRow, CoreStore};

/// Sessions & Memory: private transcripts per agent + versioned
/// memory with source labels (mandate 2.1, 9.4, 10.1-10.4).
pub fn sessions_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Sessions & Memory");
    let agents = state.store.list_agents(false).unwrap_or_default();
    if state.sessions_selected_agent.is_empty() {
        state.sessions_selected_agent = agents.first()
            .map(|a| a.id.clone()).unwrap_or_default();
    }
    egui::ComboBox::from_label("Agent")
        .selected_text(&state.sessions_selected_agent)
        .show_ui(ui, |ui| {
            for a in &agents {
                if ui.selectable_label(
                    a.id == state.sessions_selected_agent, &a.id)
                    .clicked() {
                    state.sessions_selected_agent = a.id.clone();
                    state.sessions_selected_session.clear();
                }
            }
        });
    let agent_id = state.sessions_selected_agent.clone();
    if agent_id.is_empty() {
        ui.label("(no agents - create one first)");
        return;
    }
    ui.add_space(6.0);

    // Sessions + transcript.
    let sessions = state.store.list_sessions(&agent_id)
        .unwrap_or_default();
    if state.sessions_selected_session.is_empty() {
        state.sessions_selected_session = sessions.last()
            .map(|s| s.id.clone()).unwrap_or_default();
    }
    ui.horizontal(|ui| {
        egui::ComboBox::from_label("Session")
            .selected_text(&state.sessions_selected_session)
            .show_ui(ui, |ui| {
                for s in &sessions {
                    if ui.selectable_label(
                        s.id == state.sessions_selected_session,
                        format!("{} [{}]", s.id, s.status)).clicked() {
                        state.sessions_selected_session =
                            s.id.clone();
                    }
                }
            });
    });
    let session_id = state.sessions_selected_session.clone();
    if !session_id.is_empty() {
        egui::ScrollArea::vertical().id_salt("transcript-scroll")
            .max_height(180.0).show(ui, |ui| {
            egui::Grid::new("transcript").striped(true)
                .num_columns(3).show(ui, |ui| {
                    ui.strong("Seq"); ui.strong("Role");
                    ui.strong("Content"); ui.end_row();
                    for m in state.store.transcript(&session_id)
                        .unwrap_or_default() {
                        ui.label(m.seq.to_string());
                        ui.label(&m.role);
                        ui.label(m.content.chars()
                                 .take(160).collect::<String>());
                        ui.end_row();
                    }
                });
        });
    } else {
        ui.label("(no sessions yet)");
    }
    ui.separator();

    // Memory: versioned, with source and provenance (19.4).
    ui.heading("Memory");
    egui::ScrollArea::vertical().id_salt("memory-scroll")
        .max_height(140.0).show(ui, |ui| {
        egui::Grid::new("memory").striped(true).num_columns(5)
            .show(ui, |ui| {
                ui.strong("Category"); ui.strong("Content");
                ui.strong("Source"); ui.strong("Version");
                ui.strong("Action"); ui.end_row();
                for m in state.store.list_memory(&agent_id)
                    .unwrap_or_default() {
                    ui.label(&m.category);
                    ui.label(m.content.chars().take(120)
                             .collect::<String>());
                    ui.label(&m.source);
                    ui.label(format!("v{}", m.version));
                    if ui.button(format!("delete-memory-{}", m.id))
                        .clicked() {
                        let _ = state.store.delete_memory(m.id);
                        state.status_line =
                            format!("Memory {} deleted", m.id);
                    }
                    ui.end_row();
                }
            });
    });
    egui::Grid::new("add-memory").num_columns(2).show(ui, |ui| {
        ui.label("Category");
        ui.add(egui::TextEdit::singleline(&mut state.memory_category)
            .hint_text("e.g. fact"));
        ui.end_row();
        ui.label("Content");
        ui.add(egui::TextEdit::multiline(&mut state.memory_content)
            .hint_text("Memory content"));
        ui.end_row();
    });
    if ui.button("Add memory").clicked() {
        let cat = state.memory_category.trim().to_string();
        let content = state.memory_content.trim().to_string();
        if cat.is_empty() || content.is_empty() {
            state.status_line = "Category and content are required"
                .into();
        } else {
            let now = las_core::storage::utc_now();
            let _ = state.store.add_memory(
                &las_core::storage::MemoryRow {
                    id: 0, agent_id: agent_id.clone(),
                    category: cat, content, source: "user".into(),
                    provenance: "sessions screen".into(),
                    confidence: 1.0, scope: "private".into(),
                    version: 1, created_at: now.clone(),
                    updated_at: now, expires_at: String::new(),
                });
            state.status_line = "Memory added".into();
            state.memory_content.clear();
        }
    }
}

/// Collaboration Channels (mandate 7.6): membership, posting, feeds.
pub fn channels_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Collaboration Channels");
    let store = state.store.clone();

    // Create channel.
    egui::Grid::new("new-channel").num_columns(2).show(ui, |ui| {
        ui.label("Channel id");
        ui.add(egui::TextEdit::singleline(
            &mut state.channel_new_id).hint_text("e.g. general"));
        ui.end_row();
        ui.label("Name");
        ui.add(egui::TextEdit::singleline(
            &mut state.channel_new_name).hint_text("Display name"));
        ui.end_row();
    });
    if ui.button("Create channel").clicked() {
        let id = state.channel_new_id.trim().to_string();
        let name = state.channel_new_name.trim().to_string();
        if id.is_empty() || name.is_empty() {
            state.status_line = "Id and name are required".into();
        } else {
            match store.create_channel(&id, &name, "") {
                Ok(()) => {
                    state.status_line = format!("Channel {id} created");
                    state.channel_selected = id;
                    state.channel_new_id.clear();
                    state.channel_new_name.clear();
                }
                Err(e) => state.status_line = e.to_string(),
            }
        }
    }
    ui.separator();

    let channels = store.list_channels().unwrap_or_default();
    if channels.is_empty() {
        ui.label("(no channels yet)");
        return;
    }
    if state.channel_selected.is_empty() {
        state.channel_selected = channels[0].0.clone();
    }
    ui.horizontal(|ui| {
        egui::ComboBox::from_label("Channel")
            .selected_text(&state.channel_selected)
            .show_ui(ui, |ui| {
                for (id, name, _) in &channels {
                    if ui.selectable_label(
                        *id == state.channel_selected,
                        format!("{id} ({name})")).clicked() {
                        state.channel_selected = id.clone();
                    }
                }
            });
    });
    let selected = state.channel_selected.clone();
    let members = store.channel_members(&selected)
        .unwrap_or_default();
    ui.label(format!("Members: {}",
                     members.join(", ")));

    // Join with an agent.
    let agents = store.list_agents(false).unwrap_or_default();
    if state.channel_join_agent.is_empty() {
        state.channel_join_agent = agents.first()
            .map(|a| a.id.clone()).unwrap_or_default();
    }
    ui.horizontal(|ui| {
        egui::ComboBox::from_label("Join as")
            .selected_text(&state.channel_join_agent)
            .show_ui(ui, |ui| {
                for a in &agents {
                    if ui.selectable_label(
                        a.id == state.channel_join_agent, &a.id)
                        .clicked() {
                        state.channel_join_agent = a.id.clone();
                    }
                }
            });
        if ui.button("Join channel").clicked() {
            let _ = store.join_channel(&selected,
                                       &state.channel_join_agent);
            state.status_line = format!(
                "{} joined {selected}", state.channel_join_agent);
        }
    });
    ui.separator();

    // Posts feed.
    egui::ScrollArea::vertical().id_salt("posts-scroll")
        .max_height(160.0).show(ui, |ui| {
        egui::Grid::new("posts").striped(true).num_columns(2)
            .show(ui, |ui| {
                ui.strong("Author"); ui.strong("Content");
                ui.end_row();
                for (_, author, content, _) in
                    store.channel_posts(&selected).unwrap_or_default() {
                    ui.label(&author);
                    ui.label(content.chars().take(160)
                             .collect::<String>());
                    ui.end_row();
                }
            });
    });
    egui::Grid::new("post-form").num_columns(2).show(ui, |ui| {
        ui.label("Post as");
        ui.add(egui::TextEdit::singleline(
            &mut state.channel_post_author)
            .hint_text("Agent id"));
        ui.end_row();
        ui.label("Message");
        ui.add(egui::TextEdit::multiline(
            &mut state.channel_post_content)
            .hint_text("Channel message"));
        ui.end_row();
    });
    if ui.button("Post to channel").clicked() {
        let author = state.channel_post_author.trim().to_string();
        let content = state.channel_post_content.trim().to_string();
        match store.post_channel(&selected, &author, &content, 0) {
            Ok(id) => {
                state.status_line =
                    format!("Post {id} published");
                state.channel_post_content.clear();
            }
            Err(e) => state.status_line = e.to_string(),
        }
    }
}

/// Teams / Network: the communication topology editor (mandate
/// section 8) - grants are explicit, default-deny between agents.
pub fn teams_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Agent Teams / Network");
    let store = state.store.clone();

    // Team overview.
    let agents = store.list_agents(true).unwrap_or_default();
    egui::Grid::new("team").striped(true).num_columns(4).show(ui, {
        |ui| {
            ui.strong("Agent"); ui.strong("Status");
            ui.strong("Enabled"); ui.strong("Model"); ui.end_row();
            for a in &agents {
                ui.label(&a.id);
                ui.label(&a.status);
                ui.label(if a.enabled { "yes" } else { "no" });
                let model = store.agent(&a.id).map(|ag| format!(
                    "{}/{}", ag.profile.model_provider,
                    ag.profile.model_id)).unwrap_or_default();
                ui.label(model);
                ui.end_row();
            }
        }
    });
    ui.separator();

    // Topology editor.
    ui.heading("Communication rules");
    ui.label(
        "Agents default-deny each other. Grant message, call and \
         delegate paths explicitly; user->agent is allowed by default.");
    egui::Grid::new("rule-form").num_columns(2).show(ui, |ui| {
        ui.label("From agent");
        ui.add(egui::TextEdit::singleline(&mut state.rule_src)
            .hint_text("source agent id"));
        ui.end_row();
        ui.label("To agent");
        ui.add(egui::TextEdit::singleline(&mut state.rule_dst)
            .hint_text("destination agent id"));
        ui.end_row();
    });
    ui.horizontal(|ui| {
        ui.checkbox(&mut state.rule_message, "may message");
        ui.checkbox(&mut state.rule_call, "may call");
        ui.checkbox(&mut state.rule_delegate, "may delegate");
        if ui.button("Save rule").clicked() {
            let rule = CommRuleRow {
                src: state.rule_src.trim().into(),
                dst: state.rule_dst.trim().into(),
                may_message: state.rule_message,
                may_call: state.rule_call,
                may_delegate: state.rule_delegate,
            };
            if rule.src.is_empty() || rule.dst.is_empty() {
                state.status_line =
                    "From and to are required".into();
            } else {
                match store.set_comm_rule(&rule) {
                    Ok(()) => state.status_line = format!(
                        "Rule {} -> {} saved", rule.src, rule.dst),
                    Err(e) => state.status_line = e.to_string(),
                }
            }
        }
    });
    ui.add_space(6.0);
    let rules = store.list_comm_rules().unwrap_or_default();
    egui::ScrollArea::vertical().id_salt("rules-scroll")
        .max_height(160.0).show(ui, |ui| {
        egui::Grid::new("rules").striped(true).num_columns(5)
            .show(ui, |ui| {
                ui.strong("From"); ui.strong("To");
                ui.strong("Message"); ui.strong("Call");
                ui.strong("Delegate"); ui.end_row();
                for r in &rules {
                    ui.label(&r.src);
                    ui.label(&r.dst);
                    ui.label(yes_no(r.may_message));
                    ui.label(yes_no(r.may_call));
                    ui.label(yes_no(r.may_delegate));
                    ui.end_row();
                }
                if rules.is_empty() {
                    ui.label("(no explicit rules - default deny)");
                    ui.end_row();
                }
            });
    });
}

fn yes_no(b: bool) -> &'static str {
    if b { "yes" } else { "no" }
}

/// Convenience for tests: build a state over a fresh in-memory store.
#[allow(dead_code)]
pub fn test_state(store: std::sync::Arc<CoreStore>) -> AppState {
    AppState::from_store(store)
}
