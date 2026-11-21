//! Desktop shell (UI-stack decision evidence, mandate 4.2).
//!
//! Smoke mode: `LAS_SMOKE_FRAMES=N` renders N frames then closes, so a
//! headless CI can prove the window really opens and paints.

fn main() {
    eframe::run_native(
        "Local Agent Studio",
        eframe::NativeOptions::default(),
        Box::new(|_cc| Ok(Box::new(Shell::default()))),
    )
    .expect("egui window");
}

#[derive(Default)]
struct Shell {
    frames: u64,
}

impl eframe::App for Shell {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("Local Agent Studio");
            ui.label("Rust desktop shell");
        });
        self.frames += 1;
        if let Ok(limit) = std::env::var("LAS_SMOKE_FRAMES") {
            if let Ok(n) = limit.parse::<u64>() {
                if self.frames >= n {
                    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                }
            }
        }
    }
}
