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
                    if ui.button(format!(
                        "revoke-{}->{}", r.src, r.dst)).clicked() {
                        let _ = store.delete_comm_rule(&r.src,
                                                       &r.dst);
                        state.status_line = format!(
                            "Revoked {} -> {}: default-deny again",
                            r.src, r.dst);
                    }
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

/// Providers & Models (mandate section 12): register real
/// OpenAI-compatible endpoints, list their models over REAL HTTP,
/// probe capabilities with a real chat call, and start/stop the
/// runtime that drives actual model turns. Keys are referenced by
/// environment-variable NAME only; the secret is read at runtime
/// start and never persisted.
pub fn providers_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Providers & Models");
    let store = state.store.clone();

    // Background HTTP results arrive through shared slots; repaint so
    // the UI notices them without user interaction.
    ui.ctx().request_repaint_after(std::time::Duration::
                                    from_millis(150));

    // Register endpoint.
    egui::Grid::new("endpoint-form").num_columns(2).show(ui, |ui| {
        ui.label("Name");
        ui.add(egui::TextEdit::singleline(&mut state.endpoint_name)
            .hint_text("e.g. lmstudio"));
        ui.end_row();
        ui.label("Base URL");
        ui.add(egui::TextEdit::singleline(&mut state.endpoint_url)
            .hint_text("http://127.0.0.1:1234"));
        ui.end_row();
        ui.label("API key env var (optional)");
        ui.add(egui::TextEdit::singleline(
            &mut state.endpoint_key_env)
            .hint_text("LAS_LMSTUDIO_KEY"));
        ui.end_row();
    });
    if ui.button("Save endpoint").clicked() {
        let name = state.endpoint_name.trim().to_string();
        let url = state.endpoint_url.trim().to_string();
        if name.is_empty() || url.is_empty() {
            state.status_line = "Name and base URL are required"
                .into();
        } else if !url.starts_with("http://")
            && !url.starts_with("https://") {
            state.status_line = "Base URL must start with http:// \
                                 or https://".into();
        } else {
            let id = name.to_lowercase().replace(
                |c: char| !c.is_ascii_alphanumeric(), "-");
            let _ = store.upsert_endpoint(
                &las_core::storage::EndpointRow {
                    id, name, base_url: url,
                    api_key_env: state.endpoint_key_env.trim()
                        .to_string(),
                    note: String::new(),
                    created_at: las_core::storage::utc_now(),
                });
            state.status_line = "Endpoint saved".into();
        }
    }
    ui.separator();

    // Endpoints with REAL model discovery.
    let endpoints = store.list_endpoints().unwrap_or_default();
    if endpoints.is_empty() {
        ui.label("(no endpoints registered yet)");
    } else {
        egui::Grid::new("endpoints").striped(true).num_columns(5)
            .show(ui, |ui| {
                ui.strong("Name"); ui.strong("Base URL");
                ui.strong("Key env"); ui.strong("Models");
                ui.strong("Action"); ui.end_row();
                for e in &endpoints {
                    ui.label(&e.name);
                    ui.label(&e.base_url);
                    ui.label(if e.api_key_env.is_empty() {
                        "(none)".to_string()
                    } else { e.api_key_env.clone() });
                    if ui.button(format!(
                        "list-models-{}", e.id)).clicked() {
                        let url = e.base_url.clone();
                        let key_env = e.api_key_env.clone();
                        let sink = state.provider_models.clone();
                        let note = state.provider_note.clone();
                        std::thread::spawn(move || {
                            let key = if key_env.is_empty() { None }
                                else { std::env::var(&key_env).ok() };
                            let adapter =
                                las_core::providers::
                                OpenAiCompatAdapter::new(&url, key);
                            match adapter {
                                Ok(a) => match a.list_models() {
                                    Ok(models) => {
                                        let ids: Vec<String> =
                                            models.iter().map(|m| m.id
                                                .clone()).collect();
                                        *note.lock().unwrap() =
                                            format!("{} model(s) \
                                                     discovered",
                                                    ids.len());
                                        *sink.lock().unwrap() = ids;
                                    }
                                    Err(err) => {
                                        *note.lock().unwrap() =
                                            format!("error: {err}");
                                    }
                                },
                                Err(err) => {
                                    *note.lock().unwrap() =
                                        format!("error: {err}");
                                }
                            }
                        });
                    }
                    if ui.button(format!(
                        "remove-endpoint-{}", e.id)).clicked() {
                        let _ = store.delete_endpoint(&e.id);
                        state.status_line = format!(
                            "Endpoint {} removed", e.name);
                    }
                    ui.end_row();
                }
            });
        let note = state.provider_note.lock().unwrap().clone();
        if !note.is_empty() { ui.label(note); }

        // Discovered models with a REAL capability probe each.
        let models = state.provider_models.lock().unwrap().clone();
        if !models.is_empty() {
            ui.add_space(4.0);
            ui.label("Discovered models (probe = real chat call):");
            egui::Grid::new("models").striped(true).num_columns(2)
                .show(ui, |ui| {
                    ui.strong("Model"); ui.strong("Probe");
                    ui.end_row();
                    for m in &models {
                        ui.label(m);
                        if ui.button(format!("probe-{m}"))
                            .clicked() {
                            let url = endpoints[0].base_url.clone();
                            let model = m.clone();
                            let log = state.probe_log.clone();
                            std::thread::spawn(move || {
                                let adapter =
                                    las_core::providers::
                                    OpenAiCompatAdapter::new(
                                        &url, None);
                                let line = match adapter {
                                    Ok(a) => match a.probe(&model) {
                                        Ok(r) => format!(
                                            "{model}: OK - \
                                             {} chars, {}/{} tokens",
                                            r.content.len(),
                                            r.tokens_in,
                                            r.tokens_out),
                                        Err(e) => format!(
                                            "{model}: FAILED - {e}"),
                                    },
                                    Err(e) => format!(
                                        "{model}: FAILED - {e}"),
                                };
                                log.lock().unwrap().push(line);
                            });
                        }
                        ui.end_row();
                    }
                });
            let log = state.probe_log.lock().unwrap().clone();
            for line in log.iter().rev().take(6) {
                ui.label(line);
            }
        }
    }
    ui.separator();

    // Runtime: real model turns through the fair scheduler.
    ui.heading("Runtime");
    match &state.runtime {
        Some(handle) => {
            let status = handle.status.lock().unwrap().clone();
            ui.label(format!("Runtime {status}"));
            if ui.button("Stop runtime").clicked() {
                if let Some(mut h) = state.runtime.take() {
                    h.stop();
                }
                state.status_line = "Runtime stopped".into();
            }
        }
        None => {
            ui.label("Runtime not started - starting it connects \
                      every agent turn to its provider endpoint \
                      through the fair scheduler.");
            if ui.button("Start runtime").clicked() {
                match crate::bootstrap::RuntimeHandle::start(
                    store.clone()) {
                    Ok(handle) => {
                        state.status_line = "Runtime started"
                            .into();
                        state.runtime = Some(handle);
                    }
                    Err(e) => state.status_line =
                        format!("start failed: {e}"),
                }
            }
        }
    }
}

/// Activity & History: what actually happened - recent deliveries
/// from the agent store plus the VERIFIED legacy run history written
/// by the Python engine on this data directory.
pub fn history_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Activity & History");
    let store = state.store.clone();

    ui.label("Recent message deliveries:");
    let recent = store.recent_inbox(20).unwrap_or_default();
    egui::ScrollArea::vertical().id_salt("activity-scroll")
        .max_height(180.0).show(ui, |ui| {
            egui::Grid::new("activity").striped(true)
                .num_columns(5).show(ui, |ui| {
                    ui.strong("Id"); ui.strong("From");
                    ui.strong("To"); ui.strong("Kind");
                    ui.strong("State"); ui.end_row();
                    for e in &recent {
                        ui.label(e.id.to_string());
                        ui.label(&e.sender);
                        ui.label(&e.recipient);
                        ui.label(&e.kind);
                        ui.label(&e.delivery_state);
                        ui.end_row();
                    }
                    if recent.is_empty() {
                        ui.label("(nothing delivered yet)");
                        ui.end_row();
                    }
                });
        });
    ui.separator();

    ui.label("Legacy runs (written by the Python engine on this \
              data directory; the hash chain is verified on read):");
    let runs = las_core::migration::legacy_runs(&store)
        .unwrap_or_default();
    if runs.is_empty() {
        ui.label("(no legacy runs found)");
    } else {
        if state.history_selected_run.is_empty() {
            state.history_selected_run = runs[0].0.clone();
        }
        egui::Grid::new("runs").striped(true).num_columns(4)
            .show(ui, |ui| {
                ui.strong("Run"); ui.strong("Events");
                ui.strong("Last event"); ui.strong("Action");
                ui.end_row();
                for (run_id, count, last) in &runs {
                    ui.label(run_id);
                    ui.label(count.to_string());
                    ui.label(last);
                    if ui.button(format!("open-{run_id}"))
                        .clicked() {
                        state.history_selected_run =
                            run_id.clone();
                    }
                    ui.end_row();
                }
            });
        let selected = state.history_selected_run.clone();
        ui.add_space(4.0);
        ui.label(format!("Events of {selected} (newest first):"));
        match las_core::migration::legacy_events(&store, &selected) {
            Ok(events) => {
                ui.label(format!(
                    "hash chain verified - {} events intact",
                    events.len()));
                egui::ScrollArea::vertical()
                    .id_salt("events-scroll")
                    .max_height(200.0).show(ui, |ui| {
                        egui::Grid::new("events").striped(true)
                            .num_columns(3).show(ui, |ui| {
                                ui.strong("Seq");
                                ui.strong("Type");
                                ui.strong("When"); ui.end_row();
                                for e in events.iter().rev() {
                                    ui.label(e.seq.to_string());
                                    ui.label(&e.kind);
                                    ui.label(&e.ts);
                                    ui.end_row();
                                }
                            });
                    });
            }
            Err(e) => {
                // A broken chain is reported, never hidden.
                ui.label(format!("CHAIN REFUSED: {e}"));
            }
        }
    }
}

/// The tool vocabulary (mandate 10.1): what each grant means and
/// which agent currently holds it. Grants are the AUTHORIZATION
/// model; the execution engine is on the roadmap and this screen
/// says so honestly instead of faking a toggle.
const TOOL_DOCS: [(&str, &str); 4] = [
    ("read_file", "read a file inside the workspace"),
    ("write_file", "create or modify a file inside the workspace"),
    ("list_files", "enumerate the workspace tree"),
    ("web_fetch", "fetch a URL through the hardened fetch path"),
];

pub fn tools_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Tools & Integrations");
    let store = state.store.clone();

    ui.label("Grant vocabulary (mandate 10.1: nothing is granted \
              implicitly - a grant is explicit per agent):");
    egui::Grid::new("tool-vocab").striped(true).num_columns(2)
        .show(ui, |ui| {
            ui.strong("Tool"); ui.strong("What it allows");
            ui.end_row();
            for (tool, doc) in TOOL_DOCS {
                ui.label(tool);
                ui.label(doc);
                ui.end_row();
            }
        });
    ui.add_space(6.0);

    // The approval gate (TLS-2): ask-mode tool calls land here.
    ui.heading("Pending approvals");
    let pending = store.pending_approvals().unwrap_or_default();
    if pending.is_empty() {
        ui.label("Nothing waiting. Ask-once/ask-each tool calls \
                  appear here for a decision.");
    } else {
        // Vertical groups, not a wide grid: a grid row with the
        // full args JSON pushes its buttons off narrow viewports
        // (the UI test caught exactly that).
        for (id, agent, tool, args, at) in &pending {
            ui.group(|ui| {
                ui.label(format!("#{id}  agent {agent}  tool {tool}"));
                ui.label(format!("args: {args}"));
                ui.label(format!("requested {at}"));
                ui.horizontal(|ui| {
                    if ui.button(format!("approve-{id}"))
                        .clicked() {
                            let _ = store.approve_tool_request(*id);
                        }
                    if ui.button(format!("deny-{id}")).clicked() {
                        let _ = store.resolve_tool_approval(
                            *id, "denied");
                    }
                });
            });
        }
    }
    ui.add_space(8.0);

    ui.label("Per-agent grant modes (click a cell to cycle: off -> \
              always -> ask once -> ask each -> never):");
    let agents = store.list_agents(true).unwrap_or_default();
    egui::Grid::new("tool-grants").striped(true)
        .num_columns(crate::KNOWN_TOOLS.len() + 1)
        .show(ui, |ui| {
            ui.strong("Agent");
            for tool in crate::KNOWN_TOOLS {
                ui.strong((*tool).to_string());
            }
            ui.end_row();
            if agents.is_empty() {
                ui.label("(no agents)");
                ui.end_row();
            }
            for a in &agents {
                ui.label(&a.name);
                let profile = store.agent(&a.id)
                    .map(|ag| ag.profile).unwrap();
                let mut grants = las_core::tools::parse_grants(
                    &profile.tool_grants_json);
                for tool in crate::KNOWN_TOOLS {
                    let mode = grants.iter()
                        .find(|g| g.tool == *tool)
                        .map(|g| g.mode);
                    let label = match mode {
                        None => "-".to_string(),
                        Some(m) => m.as_str().replace('_', " "),
                    };
                    if ui.button(format!(
                        "cycle-{}-{}", a.id, tool)).clicked() {
                        // off -> always -> ask_once -> ask_each ->
                        // never -> off (removed).
                        use las_core::tools::{GrantMode, ToolGrant};
                        let next = match mode {
                            None => Some(GrantMode::Always),
                            Some(GrantMode::Always) =>
                                Some(GrantMode::AskOnce),
                            Some(GrantMode::AskOnce) =>
                                Some(GrantMode::AskEach),
                            Some(GrantMode::AskEach) =>
                                Some(GrantMode::Never),
                            Some(GrantMode::Never) => None,
                        };
                        match next {
                            Some(m) => {
                                if let Some(g) = grants.iter_mut()
                                    .find(|g| g.tool == *tool) {
                                    g.mode = m;
                                } else {
                                    grants.push(ToolGrant {
                                        tool: (*tool).into(),
                                        mode: m });
                                }
                            }
                            None => {
                                grants.retain(|g| g.tool != *tool);
                            }
                        }
                        let _ = store.set_tool_grants(
                            &a.id, &las_core::tools::grants_to_json(
                                &grants));
                    }
                    ui.label(label);
                }
                ui.end_row();
            }
        });
    ui.add_space(8.0);
}

/// Projects & Workspaces (live area 10/14): projects group agents
/// and give the group a shared identity; each member's workspace
/// jail stays per-agent (mandate 10) - a project is an
/// organizational entity, not a permission bypass.
pub fn projects_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Projects");
    let store = state.store.clone();

    ui.label("A project groups agents for one effort. Membership \\
              never widens a member's tool grants or communication \\
              rules - it is organization, not permission.");
    ui.add_space(6.0);

    // Create form (stacked, not horizontal: the row must not push
    // the button off narrow viewports).
    egui::Grid::new("project-form").num_columns(2).show(ui, |ui| {
        ui.label("New project name");
        ui.add(egui::TextEdit::singleline(
            &mut state.project_name).hint_text("name"));
        ui.end_row();
        ui.label("Description");
        ui.add(egui::TextEdit::singleline(
            &mut state.project_desc).hint_text("description"));
        ui.end_row();
    });
    if ui.button("Create").clicked() {
        let name = state.project_name.trim().to_string();
        if !name.is_empty() {
            let slug: String = name.to_lowercase().chars()
                .map(|c| if c.is_alphanumeric() { c } else { '-' })
                .collect();
            let slug = slug.trim_matches('-').to_string();
            if !slug.is_empty() {
                let _ = store.create_project(
                    &slug, &name, state.project_desc.trim());
                state.project_name.clear();
                state.project_desc.clear();
            }
        }
    }
    ui.add_space(8.0);

    // Project list with member management.
    let projects = store.list_projects().unwrap_or_default();
    if projects.is_empty() {
        ui.label("No projects yet.");
        return;
    }
    for (id, name, desc, created) in &projects {
        egui::CollapsingHeader::new(format!(
            "{name}  ({id})")).default_open(true).show(ui, |ui| {
            ui.label(format!(
                "created {created} - {}", desc));
            let members = store.project_members(id)
                .unwrap_or_default();
            ui.label(format!(
                "members ({}): {}",
                members.len(),
                if members.is_empty() { "(none)".to_string() }
                else { members.join(", ") }));
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(
                    &mut state.project_agent_input)
                    .hint_text("agent id"));
                if ui.button("Add member").clicked() {
                    let agent = state.project_agent_input
                        .trim().to_string();
                    if !agent.is_empty() {
                        let _ = store.add_project_member(
                            id, &agent);
                        state.project_agent_input.clear();
                    }
                }
            });
            for m in &members {
                ui.horizontal(|ui| {
                    ui.label(format!("· {m}"));
                    if ui.button(format!("remove {m}")).clicked() {
                        let _ = store.remove_project_member(id, m);
                    }
                });
            }
        });
    }
}

/// Artifacts & Files (live area 11/14): browse the REAL files in
/// each agent's workspace jail - the same tree the tool engine
/// reads and writes (mandate 10).  Read-only here; writes happen
/// through tool calls, never through this screen.
pub fn artifacts_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Artifacts & Files");
    let store = state.store.clone();
    ui.label("The workspace jail of one agent, exactly as the tool \\
              engine sees it. Files are created by tool calls; this \\
              view is read-only.");
    ui.add_space(6.0);

    ui.horizontal(|ui| {
        ui.label("Agent:");
        ui.add(egui::TextEdit::singleline(
            &mut state.artifacts_agent).hint_text("agent id"));
    });
    ui.add_space(4.0);

    let agent = state.artifacts_agent.trim().to_string();
    if agent.is_empty() {
        ui.label("Enter an agent id to browse its workspace.");
        return;
    }
    let Some(dir) = store.data_dir().map(|d| d.join("workspaces")
                                          .join(&agent)) else {
        ui.label("No workspace: this store is in-memory.");
        return;
    };
    if !dir.exists() {
        ui.label(format!(
            "No workspace yet for '{agent}' (one is created on its \\
             first granted tool call)."));
        return;
    }
    // Bounded walk, same discipline as list_files in las-core.
    let mut files: Vec<String> = Vec::new();
    let mut stack = vec![dir.clone()];
    while let Some(d) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else { continue };
        for e in entries.flatten() {
            let p = e.path();
            let shown = p.strip_prefix(&dir).unwrap_or(&p)
                .to_string_lossy().into_owned();
            if p.is_dir() {
                stack.push(p);
            } else {
                files.push(shown);
            }
            if files.len() >= 1000 { break; }
        }
        if files.len() >= 1000 { break; }
    }
    files.sort();
    if files.is_empty() {
        ui.label("Workspace is empty.");
        return;
    }
    egui::Grid::new("artifact-files").striped(true).num_columns(2)
        .show(ui, |ui| {
            ui.strong("File"); ui.strong("Size"); ui.end_row();
            for f in &files {
                if ui.selectable_label(
                    state.artifacts_selected == *f, f).clicked() {
                    state.artifacts_selected = f.clone();
                }
                let size = std::fs::metadata(dir.join(f))
                    .map(|m| m.len()).unwrap_or(0);
                ui.label(format!("{size} B"));
                ui.end_row();
            }
        });
    if !state.artifacts_selected.is_empty() {
        ui.separator();
        ui.label(format!("Preview: {}", state.artifacts_selected));
        let path = dir.join(&state.artifacts_selected);
        // Same 5 MB cap the read_file tool enforces; previews are
        // further cut at 8 KB so a label can carry them.
        match std::fs::read(&path) {
            Ok(bytes) if bytes.len()
                <= las_core::tools::MAX_WRITE_BYTES => {
                let mut text = String::from_utf8_lossy(&bytes)
                    .into_owned();
                let cap = 8 * 1024;
                if text.len() > cap {
                    text.truncate(cap);
                    text.push_str("\n(preview cut at 8 KB; the \
                                   file itself is larger)");
                }
                ui.label(text);
            }
            Ok(_) => {
                ui.label("File exceeds the 5 MB preview cap.");
            }
            Err(e) => {
                ui.label(format!("read failed: {e}"));
            }
        }
    }
}

/// Tasks & Delegations (live area 12/14): a task assigned to an
/// agent is tracked here AND delivered to the agent's real inbox,
/// so the runtime actually picks it up on its next turn.
pub fn tasks_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Tasks & Delegations");
    let store = state.store.clone();
    ui.label("Creating a task posts it to the agent's inbox - the \\
              runtime delivers it on the agent's next turn; the \\
              record here tracks the outcome.");
    ui.add_space(6.0);

    egui::Grid::new("task-form").num_columns(2).show(ui, |ui| {
        ui.label("Title");
        ui.add(egui::TextEdit::singleline(
            &mut state.task_title).hint_text("what to do"));
        ui.end_row();
        ui.label("Instructions");
        ui.add(egui::TextEdit::multiline(
            &mut state.task_instructions).hint_text("detail"));
        ui.end_row();
        ui.label("Assign to agent");
        ui.add(egui::TextEdit::singleline(
            &mut state.task_to).hint_text("agent id"));
        ui.end_row();
    });
    if ui.button("Create task").clicked() {
        let title = state.task_title.trim().to_string();
        let to = state.task_to.trim().to_string();
        if !title.is_empty() && !to.is_empty() {
            if let Ok(id) = store.create_task(
                &title, state.task_instructions.trim(),
                "user", &to) {
                let text = format!(
                    "Task #{id}: {title}\n{}",
                    state.task_instructions.trim());
                let _ = store.send_message(
                    "user", &to, &text, "",
                    las_core::communication::BusyMode::Queue);
                state.task_title.clear();
                state.task_instructions.clear();
            }
        }
    }
    ui.add_space(8.0);

    let tasks = store.list_tasks().unwrap_or_default();
    if tasks.is_empty() {
        ui.label("No tasks yet.");
        return;
    }
    egui::Grid::new("tasks").striped(true).num_columns(5).show(
        ui, |ui| {
            ui.strong("#"); ui.strong("Title");
            ui.strong("To"); ui.strong("Status");
            ui.strong("Actions"); ui.end_row();
            for (id, title, _instr, _from, to, status, created)
                in &tasks {
                ui.label(id.to_string());
                ui.label(title);
                ui.label(to);
                ui.label(format!("{status} ({created})"));
                ui.horizontal(|ui| {
                    if ui.button(format!("done-{id}")).clicked() {
                        let _ = store.set_task_status(
                            *id, "done");
                    }
                    if ui.button(format!("cancel-{id}")).clicked()
                    {
                        let _ = store.set_task_status(
                            *id, "cancelled");
                    }
                });
                ui.end_row();
            }
        });
}

/// Workflow Studio (live area 13/14): edit plans as ordered steps
/// and export them in plan schema v1 - the exact JSON the Python
/// engine runs (`python3 -m las run <file>`).
pub fn workflow_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Workflow Studio");
    let store = state.store.clone();
    ui.label("A plan is ordered steps with instructions. Export \\
              writes plan schema v1 JSON; run it with the engine \\
              CLI: python3 -m las run <exported-file>");
    ui.add_space(6.0);

    egui::Grid::new("plan-form").num_columns(2).show(ui, |ui| {
        ui.label("New plan title");
        ui.add(egui::TextEdit::singleline(
            &mut state.plan_title).hint_text("title"));
        ui.end_row();
        ui.label("Goal");
        ui.add(egui::TextEdit::singleline(
            &mut state.plan_goal).hint_text("one sentence"));
        ui.end_row();
    });
    if ui.button("Create plan").clicked() {
        let title = state.plan_title.trim().to_string();
        if !title.is_empty() {
            let slug: String = title.to_lowercase().chars()
                .map(|c| if c.is_alphanumeric() { c }
                      else { '-' }).collect();
            let slug = slug.trim_matches('-').to_string();
            if !slug.is_empty() {
                let _ = store.create_plan(
                    &slug, &title, state.plan_goal.trim());
                state.plan_selected = slug.clone();
                state.plan_title.clear();
                state.plan_goal.clear();
            }
        }
    }
    ui.add_space(8.0);

    let plans = store.list_plans().unwrap_or_default();
    if plans.is_empty() {
        ui.label("No plans yet.");
        return;
    }
    for (id, title, goal, created) in &plans {
        let header = format!("{title} ({id}, {created}, {goal})");
        egui::CollapsingHeader::new(header).default_open(
            state.plan_selected == *id).show(ui, |ui| {
            let steps = store.plan_steps(id).unwrap_or_default();
            ui.label(format!("{} steps", steps.len()));
            for (idx, stitle, sinstr) in &steps {
                ui.label(format!(
                    "  {}. {} - {}", idx + 1, stitle,
                    if sinstr.is_empty() { "" } else { sinstr }));
            }
            if ui.button(format!("add-step-{id}")).clicked() {
                state.step_edit_plan = id.clone();
                state.plan_step_title.clear();
                state.plan_step_instructions.clear();
            }
            if state.step_edit_plan == *id {
                egui::Grid::new("step-form").num_columns(2).show(
                    ui, |ui| {
                    ui.label("Step title");
                    ui.add(egui::TextEdit::singleline(
                        &mut state.plan_step_title)
                        .hint_text("step title"));
                    ui.end_row();
                    ui.label("Instructions");
                    ui.add(egui::TextEdit::multiline(
                        &mut state.plan_step_instructions)
                        .hint_text("step instructions"));
                    ui.end_row();
                });
                if ui.button(format!("save-step-{id}")).clicked() {
                    let t = state.plan_step_title.trim().to_string();
                    if !t.is_empty() {
                        let _ = store.add_plan_step(
                            id, &t,
                            state.plan_step_instructions.trim());
                        state.plan_step_title.clear();
                        state.plan_step_instructions.clear();
                        state.step_edit_plan.clear();
                    }
                }
            }
            if ui.button(format!("export-{id}")).clicked() {
                let steps = store.plan_steps(id)
                    .unwrap_or_default();
                let tasks: Vec<serde_json::Value> = steps.iter()
                    .map(|(i, t, instr)| serde_json::json!({
                        "id": format!("t{}", i + 1),
                        "type": "task",
                        "title": t,
                        "instructions": instr,
                        "needs": if *i > 0 {
                            vec![format!("t{}", i)]
                        } else { vec![] },
                        "constraints": [],
                    })).collect();
                let plan = serde_json::json!({
                    "schema_version": 1,
                    "id": id,
                    "title": title,
                    "goal": goal,
                    "created_at": created,
                    "options": {},
                    "constraints": [],
                    "tasks": tasks,
                });
                if let Some(dir) = store.data_dir() {
                    let pdir = dir.join("plans");
                    let _ = std::fs::create_dir_all(&pdir);
                    let file = pdir.join(format!("{id}.json"));
                    let _ = std::fs::write(
                        &file, plan.to_string());
                    state.plan_exported = file.to_string_lossy()
                        .into_owned();
                }
            }
        });
    }
    if !state.plan_exported.is_empty() {
        ui.separator();
        ui.label(format!("Exported: {}", state.plan_exported));
    }
}

/// Settings & Diagnostics (live area 14/14): real facts about this
/// installation - versions, data location, store integrity, row
/// counts, configured providers.
pub fn settings_screen(ui: &mut egui::Ui, state: &mut AppState) {
    ui.heading("Settings & Diagnostics");
    let store = state.store.clone();
    egui::Grid::new("diag").num_columns(2).show(ui, |ui| {
        ui.strong("Item"); ui.strong("Value"); ui.end_row();
        ui.label("Desktop version");
        ui.label(env!("CARGO_PKG_VERSION")); ui.end_row();
        ui.label("Engine");
        ui.label(format!(
            "las-core (SQLite WAL, {})",
            match store.integrity_check() {
                Ok(v) => format!("quick_check: {v}"),
                Err(e) => format!("quick_check failed: {e}"),
            })); ui.end_row();
        ui.label("Data directory");
        ui.label(store.data_dir()
            .map(|d| d.display().to_string())
            .unwrap_or_else(|| "(in-memory store)".into()));
        ui.end_row();
        for (label, table) in [("Agents", "agents"),
                               ("Messages", "session_messages"),
                               ("Inbox rows", "inbox"),
                               ("Tool events", "tool_events"),
                               ("Projects", "projects"),
                               ("Tasks", "tasks")] {
            ui.label(label);
            ui.label(store.count(table)
                .map(|n| n.to_string())
                .unwrap_or_else(|e| format!("error: {e}")));
            ui.end_row();
        }
    });
    ui.separator();
    ui.heading("Provider endpoints");
    let endpoints = store.list_endpoints().unwrap_or_default();
    if endpoints.is_empty() {
        ui.label("No provider endpoints configured (see Providers \\
                  & Models).");
    } else {
        egui::Grid::new("endpoints").striped(true).num_columns(3)
            .show(ui, |ui| {
                ui.strong("Name"); ui.strong("Base URL");
                ui.strong("Key env"); ui.end_row();
                for e in &endpoints {
                    ui.label(&e.name);
                    ui.label(&e.base_url);
                    ui.label(&e.api_key_env);
                    ui.end_row();
                }
            });
    }
}
