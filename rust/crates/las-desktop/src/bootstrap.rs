//! Runtime bootstrapping for the desktop: ONE background thread owns
//! a current-thread tokio runtime and the `AgentRuntime` with the
//! production responder (fair scheduler -> blocking HTTP).  The UI
//! stays synchronous; it shares the store and polls a status string.
//!
//! API keys are read from the environment HERE, at start time, and
//! live only in the adapter's memory - the store keeps just the env
//! var NAME (mandate 12.3).

use las_core::providers::{
    AdapterRegistry, OpenAiCompatAdapter,
};
use las_core::scheduler::InferenceScheduler;
use las_core::storage::CoreStore;
use las_core::{production_responder, AgentRuntime};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

enum Cmd {
    Shutdown,
}

pub struct RuntimeHandle {
    tx: std::sync::mpsc::Sender<Cmd>,
    running: Arc<AtomicBool>,
    pub status: Arc<Mutex<String>>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl RuntimeHandle {
    /// Build adapters from the PERSISTED endpoints and start the
    /// delivery loop. Errors honestly if no endpoint is registered or
    /// an endpoint URL is unusable.
    pub fn start(store: Arc<CoreStore>) -> Result<Self, String> {
        let endpoints = store.list_endpoints()
            .map_err(|e| e.to_string())?;
        if endpoints.is_empty() {
            return Err("no provider endpoints registered - add one \
                        on this screen first".into());
        }
        let mut adapters = AdapterRegistry::new();
        for e in &endpoints {
            let key = if e.api_key_env.is_empty() {
                None
            } else {
                std::env::var(&e.api_key_env).ok()
            };
            let adapter = OpenAiCompatAdapter::new(&e.base_url,
                                                   key)
                .map_err(|err| format!(
                    "endpoint {}: {err}", e.name))?;
            adapters.register(&e.id, adapter);
        }
        let scheduler = Arc::new(InferenceScheduler::new(2, 1));
        let running = Arc::new(AtomicBool::new(true));
        let status = Arc::new(Mutex::new(format!(
            "running - {} endpoint(s), fair scheduler \
             (2 concurrent, 1 per model)",
            endpoints.len())));
        let (tx, rx) = std::sync::mpsc::channel::<Cmd>();
        let flag = running.clone();
        let stat = status.clone();
        let handle = std::thread::spawn(move || {
            let runtime = AgentRuntime::new(
                store.clone(),
                production_responder(
                    store.clone(), scheduler,
                    Arc::new(adapters)));
            let tok = tokio::runtime::Builder::
                new_current_thread().enable_all().build();
            let Ok(tok) = tok else {
                *stat.lock().unwrap() =
                    "failed to build the tokio runtime".into();
                flag.store(false, Ordering::SeqCst);
                return;
            };
            tok.block_on(async {
                loop {
                    if !flag.load(Ordering::SeqCst) {
                        break;
                    }
                    if let Ok(Cmd::Shutdown) = rx.try_recv() {
                        break;
                    }
                    // Deliver every pending entry, oldest first; new
                    // messages (from the UI or from other agents)
                    // are picked up within one poll.
                    if let Ok(pending) = store.pending_inbox() {
                        for entry in pending {
                            let _ = runtime.deliver(&entry).await;
                        }
                    }
                    tokio::time::sleep(
                        Duration::from_millis(150)).await;
                }
                runtime.shutdown();
            });
            *stat.lock().unwrap() = "stopped".into();
            flag.store(false, Ordering::SeqCst);
        });
        Ok(RuntimeHandle { tx, running, status,
                           handle: Some(handle) })
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    pub fn stop(&mut self) {
        self.tx.send(Cmd::Shutdown).ok();
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

impl Drop for RuntimeHandle {
    fn drop(&mut self) { self.stop(); }
}
