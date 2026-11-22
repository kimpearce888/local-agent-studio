//! OpenAI-compatible provider adapters (mandate 12.1): LM Studio,
//! Ollama, llama.cpp server, vLLM, Unsloth Studio and generic
//! endpoints, on localhost or the LAN. API keys are supplied at
//! runtime from the environment and never persisted by this module.
//!
//! Verified against a real loopback HTTP server in tests (no external
//! service): the adapter's HTTP round-trip, JSON parsing and error
//! mapping are exercised for real.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug)]
pub enum ProviderError {
    Network(String),
    Status(u16, String),
    Parse(String),
    Timeout(String),
}

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>)
           -> std::fmt::Result {
        match self {
            Self::Network(e) => write!(f, "network error: {e}"),
            Self::Status(code, body) => write!(
                f, "provider returned {code}: {body}"),
            Self::Parse(e) => write!(f, "unparseable response: {e}"),
            Self::Timeout(e) => write!(f, "timeout: {e}"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

impl ChatMessage {
    pub fn new(role: &str, content: &str) -> Self {
        Self { role: role.into(), content: content.into() }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ChatResult {
    pub content: String,
    pub tokens_in: i64,
    pub tokens_out: i64,
    pub model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ModelInfo {
    pub id: String,
}

/// One OpenAI-compatible endpoint. `base_url` includes everything up
/// to (but excluding) `/v1/...`, e.g. `http://192.168.1.10:1234`.
pub struct OpenAiCompatAdapter {
    http: ureq::Agent,
    base_url: String,
    // The RESOLVED key lives only here, in memory, and this struct
    // deliberately carries no Debug/Serialize/Display impl: no log
    // line, error text or serializer may ever materialize it
    // (audit item 13; enforced by the tests below).
    api_key: Option<String>,
}

impl OpenAiCompatAdapter {
    pub fn new(base_url: &str, api_key: Option<String>)
               -> Result<Self, ProviderError> {
        Ok(Self {
            http: ureq::Agent::new_with_config(
                ureq::config::Config::builder()
                    .timeout_global(Some(Duration::from_secs(120)))
                    .timeout_per_read(Some(Duration::from_secs(30)))
                    .http_status_as_error(true)
                    .build()),
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key,
        })
    }

    fn auth(&self) -> Vec<(&'static str, String)> {
        match &self.api_key {
            Some(key) => vec![(
                "Authorization", format!("Bearer {key}"))],
            None => vec![],
        }
    }

    /// GET /v1/models - the shared model list of the endpoint.
    pub fn list_models(&self) -> Result<Vec<ModelInfo>,
                                        ProviderError> {
        #[derive(Deserialize)]
        struct Envelope { #[serde(default)] data: Vec<IdOnly> }
        #[derive(Deserialize)]
        struct IdOnly { id: String }
        let mut req = self.http.get(format!(
            "{}/v1/models", self.base_url));
        for (k, v) in self.auth() {
            req = req.header(k, v);
        }
        let mut resp = req.call().map_err(map_err)?;
        let parsed: Envelope = resp.body_mut().read_json()
            .map_err(|e| ProviderError::Parse(e.to_string()))?;
        Ok(parsed.data.into_iter()
            .map(|m| ModelInfo { id: m.id }).collect())
    }

    /// POST /v1/chat/completions (non-streaming). `params` allows
    /// temperature/max_tokens/top_p as raw JSON.
    pub fn chat(&self, model: &str, messages: &[ChatMessage],
                params: &serde_json::Value)
                -> Result<ChatResult, ProviderError> {
        #[derive(Serialize)]
        struct Req<'a> {
            model: &'a str,
            messages: &'a [ChatMessage],
            #[serde(flatten)]
            params: &'a serde_json::Value,
        }
        #[derive(Deserialize)]
        struct Resp {
            #[serde(default)]
            choices: Vec<Choice>,
            #[serde(default)]
            usage: Option<Usage>,
        }
        #[derive(Deserialize)]
        struct Choice {
            #[serde(default)]
            message: Option<Msg>,
        }
        #[derive(Deserialize)]
        struct Msg {
            #[serde(default)]
            content: Option<serde_json::Value>,
        }
        #[derive(Deserialize)]
        struct Usage {
            #[serde(default)]
            prompt_tokens: i64,
            #[serde(default)]
            completion_tokens: i64,
        }
        let body = Req { model, messages, params };
        let mut req = self.http.post(format!(
            "{}/v1/chat/completions", self.base_url));
        for (k, v) in self.auth() {
            req = req.header(k, v);
        }
        let mut resp = req.send_json(&body).map_err(map_err)?;
        let parsed: Resp = resp.body_mut().read_json()
            .map_err(|e| ProviderError::Parse(e.to_string()))?;
        let content = parsed.choices.get(0)
            .and_then(|c| c.message.as_ref())
            .and_then(|m| m.content.clone())
            .map(json_content_to_string)
            .unwrap_or_default();
        if content.is_empty() {
            return Err(ProviderError::Parse(
                "no message content in response".into()));
        }
        let usage = parsed.usage.unwrap_or(Usage {
            prompt_tokens: 0, completion_tokens: 0 });
        Ok(ChatResult {
            content,
            tokens_in: usage.prompt_tokens,
            tokens_out: usage.completion_tokens,
            model: model.to_string(),
        })
    }

    /// Real capability probe: a minimal chat call that must succeed
    /// end-to-end. Returns a verified report, never a guess.
    pub fn probe(&self, model: &str)
                 -> Result<ChatResult, ProviderError> {
        self.chat(model,
                  &[ChatMessage::new("user", "ping")],
                  &serde_json::json!({ "max_tokens": 1 }))
    }
}

fn json_content_to_string(v: serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s,
        other => other.to_string(),
    }
}

fn map_err(e: ureq::Error) -> ProviderError {
    match e {
        ureq::Error::StatusCode(code) => {
            // ureq 3 carries the status without the body; the code is
            // surfaced honestly (body capture would need
            // http_status_as_error(false) + manual handling).
            ProviderError::Status(code, String::new())
        }
        ureq::Error::Io(io) => {
            let text = io.to_string();
            if text.contains("timed out") {
                ProviderError::Timeout(text)
            } else {
                ProviderError::Network(text)
            }
        }
        // ureq 3 has further variants (http parse, tls...); all map
        // to Network with the underlying text.
        other => ProviderError::Network(other.to_string()),
    }
}

/// Named adapters for the agents' `model_provider` field.
#[derive(Default)]
pub struct AdapterRegistry {
    map: HashMap<String, Arc<OpenAiCompatAdapter>>,
}

impl AdapterRegistry {
    pub fn new() -> Self { Self::default() }

    pub fn register(&mut self, name: &str,
                    adapter: OpenAiCompatAdapter) {
        self.map.insert(name.into(), Arc::new(adapter));
    }

    pub fn get(&self, name: &str) -> Option<Arc<OpenAiCompatAdapter>> {
        self.map.get(name).cloned()
    }

    pub fn providers(&self) -> Vec<String> {
        self.map.keys().cloned().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;

    /// A real loopback HTTP server serving OpenAI-compatible routes.
    /// No external service: the adapter's full round-trip is tested.
    /// Stops when the `running` flag clears (checked between
    /// non-blocking accepts), so dropping never deadlocks.
    struct MockServer {
        base: String,
        running: std::sync::Arc<std::sync::atomic::AtomicBool>,
        handle: Option<std::thread::JoinHandle<()>>,
    }

    impl Drop for MockServer {
        fn drop(&mut self) {
            self.running.store(
                false,
                std::sync::atomic::Ordering::SeqCst);
            if let Some(h) = self.handle.take() {
                let _ = h.join();
            }
        }
    }

    fn start_mock_server(models_body: &'static str,
                         chat_body: &'static str) -> MockServer {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let addr = listener.local_addr().unwrap();
        let running = std::sync::Arc::new(
            std::sync::atomic::AtomicBool::new(true));
        let flag = running.clone();
        let handle = std::thread::spawn(move || {
            loop {
                if !flag.load(std::sync::atomic::Ordering::SeqCst) {
                    break;
                }
                match listener.accept() {
                    Ok((stream, _)) => {
                        handle_connection(stream, models_body,
                                          chat_body);
                    }
                    Err(e) if e.kind()
                        == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::
                                           from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        MockServer { base: format!("http://{addr}"), running,
                     handle: Some(handle) }
    }

    fn handle_connection(mut stream: std::net::TcpStream,
                         models_body: &'static str,
                         chat_body: &'static str) {
        {
            loop {
                let mut reader =
                    BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() { break; }
                let parts: Vec<&str> =
                    line.split_whitespace().collect();
                if parts.len() < 2 { break; }
                let (method, path) =
                    (parts[0].to_string(), parts[1].to_string());
                // headers + body (Content-Length)
                let mut content_length = 0usize;
                loop {
                    let mut h = String::new();
                    if reader.read_line(&mut h).unwrap_or(0) == 0
                        || h.trim().is_empty() { break; }
                    let lower = h.to_lowercase();
                    if let Some(v) = lower.strip_prefix(
                        "content-length:") {
                        content_length = v.trim().parse()
                            .unwrap_or(0);
                    }
                }
                let mut body = vec![0u8; content_length];
                if content_length > 0 {
                    let _ = reader.read_exact(&mut body);
                }
                let (status, content_type, payload) =
                    match (method.as_str(), path.as_str()) {
                        ("GET", "/v1/models") => (
                            "200 OK",
                            "application/json", models_body),
                        ("POST", "/v1/chat/completions") => (
                            "200 OK",
                            "application/json", chat_body),
                        _ => ("404 Not Found",
                              "application/json", "{}"),
                    };
                let resp = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: \
                     {content_type}\r\nContent-Length: {}\r\n\
                     Connection: close\r\n\r\n{payload}",
                    payload.len());
                let _ = stream.write_all(resp.as_bytes());
                let _ = stream.flush();
                break;
            }
        }
    }

    const MODELS: &str = r#"{"data":[{"id":"qwen3-8b"},
                              {"id":"llama-3.1-8b"}]}"#;
    const CHAT: &str = r#"{"choices":[{"message":{"content":
        "Hello from the model"}}],"usage":{"prompt_tokens":12,
        "completion_tokens":5}}"#;

    #[test]
    fn list_models_over_real_http() {
        let server = start_mock_server(MODELS, CHAT);
        let adapter = OpenAiCompatAdapter::new(
            &server.base, None).unwrap();
        let models = adapter.list_models().unwrap();
        assert_eq!(models.len(), 2);
        assert_eq!(models[0].id, "qwen3-8b");
    }

    /// Audit item 13: no error path may echo the resolved key.
    /// The adapter only ever puts it in the Authorization header;
    /// errors carry URLs and statuses, never headers.
    #[test]
    fn credential_never_appears_in_any_error_path() {
        const SECRET: &str = "LAS-SECRET-sentinel-8842";
        // (a) network failure (closed port): the error names the
        // URL, never the header value.
        let broken = OpenAiCompatAdapter::new(
            "http://127.0.0.1:1", Some(SECRET.into())).unwrap();
        for text in [
            broken.list_models().err().map(|e| e.to_string()),
            broken.chat("m", &[
                ChatMessage::new("user", "hi")],
                &serde_json::json!({})).err()
                .map(|e| e.to_string()),
            broken.probe("m").err().map(|e| e.to_string()),
        ].into_iter().flatten() {
            assert!(!text.contains(SECRET),
                    "error leaks the credential: {text}");
        }
        // (b) HTTP 401 from a real local endpoint: the status error
        // shows the code, never the header value.
        let listener = std::net::TcpListener::bind(
            "127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            use std::io::{Read as _, Write as _};
            if let Ok((mut s, _)) = listener.accept() {
                let mut buf = [0u8; 4096];
                let _ = s.read(&mut buf);
                let body = b"{\"error\":{\"message\":\"bad key\"}}";
                let _ = s.write_all(format!(
                    "HTTP/1.1 401 Unauthorized\r\nContent-Type: \
                     application/json\r\nContent-Length: {}\r\n\
                     Connection: close\r\n\r\n",
                    body.len()).as_bytes());
                let _ = s.write_all(body);
            }
        });
        let denied = OpenAiCompatAdapter::new(
            &format!("http://{addr}"), Some(SECRET.into())).unwrap();
        let text = denied.list_models().err()
            .map(|e| e.to_string()).unwrap_or_default();
        assert!(text.contains("401"), "status surfaced: {text}");
        assert!(!text.contains(SECRET), "leak: {text}");
    }

    #[test]
    fn chat_over_real_http_with_usage() {
        let server = start_mock_server(MODELS, CHAT);
        let adapter = OpenAiCompatAdapter::new(
            &server.base, Some("secret".into())).unwrap();
        let result = adapter.chat("qwen3-8b", &[
            ChatMessage::new("system", "You are helpful."),
            ChatMessage::new("user", "hi"),
        ], &serde_json::json!({"temperature": 0.2})).unwrap();
        assert_eq!(result.content, "Hello from the model");
        assert_eq!(result.tokens_in, 12);
        assert_eq!(result.tokens_out, 5);
        assert_eq!(result.model, "qwen3-8b");
    }

    #[test]
    fn probe_verifies_end_to_end() {
        let server = start_mock_server(MODELS, CHAT);
        let adapter = OpenAiCompatAdapter::new(
            &server.base, None).unwrap();
        assert!(adapter.probe("qwen3-8b").is_ok());
        // Unknown route => honest failure, not a guessed capability.
        let broken = OpenAiCompatAdapter::new(
            "http://127.0.0.1:1", None).unwrap();
        assert!(matches!(broken.probe("m"),
            Err(ProviderError::Network(_))));
    }

    #[test]
    fn registry_resolves_by_provider_name() {
        let server = start_mock_server(MODELS, CHAT);
        let mut reg = AdapterRegistry::new();
        reg.register("lmstudio", OpenAiCompatAdapter::new(
            &server.base, None).unwrap());
        assert!(reg.get("lmstudio").is_some());
        assert!(reg.get("ollama").is_none());
        assert_eq!(reg.providers(), vec!["lmstudio".to_string()]);
    }
}
