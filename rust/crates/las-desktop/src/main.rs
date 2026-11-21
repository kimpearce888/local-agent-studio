//! Desktop shell binary. The window opens on a real desktop; in
//! restricted environments set LAS_SMOKE_FRAMES=N to render N frames
//! and close (ADR-003 evidence path).

fn main() {
    let dir = std::env::var("LAS_DATA_DIR").unwrap_or_else(|_| {
        let home = std::env::var("HOME")
            .unwrap_or_else(|_| ".".into());
        format!("{home}/.local-agent-studio")
    });
    let _ = std::fs::create_dir_all(&dir);
    let store = match las_core::CoreStore::open(format!(
        "{dir}/studio.db")) {
        Ok(s) => std::sync::Arc::new(s),
        Err(e) => {
            eprintln!("Cannot open {dir}/studio.db: {e}");
            std::process::exit(1);
        }
    };
    let app = las_desktop::ShellApp::new(
        las_desktop::AppState::from_store(store));
    eframe::run_native(
        "Local Agent Studio",
        eframe::NativeOptions::default(),
        Box::new(|_cc| Ok(Box::new(app))),
    ).expect("egui window");
}
