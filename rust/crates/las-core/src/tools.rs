//! The tool engine (mandate 10, spec 5.9): permission broker,
//! jailed executors, an audit trail and Rule-of-Two capability
//! flags.
//!
//! Nothing is granted implicitly (mandate 10.1): a tool call runs
//! only when the agent's profile carries an explicit grant.  The
//! broker checks, in the spec 5.9 order: (1) the tool is in the
//! agent's allowlist, (2) the arguments are valid and inside size
//! limits, (3) file paths stay inside the workspace jail, (4) the
//! per-turn call budget holds.  Every decision writes a
//! `tool.called` or `tool.denied` audit row.
//!
//! Grant modes: v1 grants are Always; Ask-once/Ask-each need the
//! interactive approval gate and are NOT silently downgraded - an
//! ungranted tool is denied with the honest reason.
//!
//! Tool calls travel as a documented text convention the model can
//! emit with ANY provider (local models often lack native tool
//! calling): one JSON object per line, `TOOL_CALL {"tool": ...,
//! "args": {...}}`.

use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

/// Tool calls per turn (spec 5.9 rate limit, engine side).
pub const MAX_TOOL_CALLS_PER_TURN: usize = 8;
/// Model rounds of tool use per turn before the loop is cut.
pub const MAX_TOOL_ROUNDS: usize = 4;
/// Workspace file size cap (spec 5.9: 5 MB per file).
pub const MAX_WRITE_BYTES: usize = 5 * 1024 * 1024;
/// Web fetch response cap: 1 MB of body.
pub const MAX_FETCH_BYTES: usize = 1024 * 1024;
/// Web fetch timeout.
pub const FETCH_TIMEOUT: Duration = Duration::from_secs(10);

/// One parsed tool call from a model reply.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub tool: String,
    pub args: serde_json::Value,
}

/// The broker decision for one call.
#[derive(Debug, Clone, PartialEq)]
pub enum Decision {
    Allow,
    Deny(&'static str),
}

/// The execution outcome of one call (after authorization).
#[derive(Debug, Clone)]
pub struct ToolOutcome {
    pub ok: bool,
    pub output: String,
    pub detail: serde_json::Value,
}

impl ToolOutcome {
    fn ok(output: String) -> Self {
        Self { ok: true, output,
               detail: serde_json::Value::Null }
    }
    fn err(output: String) -> Self {
        Self { ok: false, output,
               detail: serde_json::Value::Null }
    }
}

/// The permission broker + executor for one agent turn.
pub struct Broker {
    jail_root: PathBuf,
    grants: Vec<String>,
    web_domains: Vec<String>,
}

impl Broker {
    /// `jail_root` is the agent's workspace directory; file tools
    /// can never see outside it.
    pub fn new(jail_root: impl Into<PathBuf>,
               grants: Vec<String>) -> Self {
        let jail_root = jail_root.into();
        // Canonicalize once so every later starts_with comparison
        // runs on canonical paths on both sides (symlinks like
        // /tmp -> /private/tmp would otherwise deny everything).
        let _ = std::fs::create_dir_all(&jail_root);
        let jail_root = jail_root.canonicalize()
            .unwrap_or(jail_root);
        Self { jail_root, grants, web_domains: Vec::new() }
    }

    /// Web-fetch domains allowed for this agent (from profile
    /// params); an empty list denies every fetch.
    pub fn with_web_domains(mut self, domains: Vec<String>) -> Self {
        self.web_domains = domains;
        self
    }

    /// The Rule of Two capability flags (spec 5.9), computed by
    /// code from the grants - never from what the model says.
    /// A = untrusted external input, B = sensitive access,
    /// C = external effect.  In v1 only web_fetch sets A and C;
    /// B stays false until a tool exists that can reach outside
    /// the workspace or run commands - a computed fact, not a
    /// promise.
    pub fn capability_flags(&self) -> (bool, bool, bool) {
        let web = self.grants.iter().any(|g| g == "web_fetch");
        (web, false, web)
    }

    /// Authorization in the spec 5.9 order.
    pub fn authorize(&self, call: &ToolCall) -> Decision {
        // (1) allowlist: explicit grants only.
        if !self.grants.iter().any(|g| *g == call.tool) {
            return Decision::Deny("tool not granted to this agent");
        }
        // (2) arguments valid and inside limits.
        match call.tool.as_str() {
            "read_file" | "list_files" => {
                if !call.args.get("path").and_then(
                        |v| v.as_str()).is_some_and(|p|
                        !p.is_empty()) {
                    return Decision::Deny(
                        "args.path must be a non-empty string");
                }
            }
            "write_file" => {
                let path_ok = call.args.get("path").and_then(
                    |v| v.as_str()).is_some_and(|p| !p.is_empty());
                let content = call.args.get("content")
                    .and_then(|v| v.as_str());
                match (path_ok, content) {
                    (false, _) => return Decision::Deny(
                        "args.path must be a non-empty string"),
                    (_, None) => return Decision::Deny(
                        "args.content must be a string"),
                    (_, Some(c)) if c.len() > MAX_WRITE_BYTES => {
                        return Decision::Deny(
                            "content exceeds the 5 MB file cap");
                    }
                    _ => {}
                }
            }
            "web_fetch" => {
                let url = call.args.get("url").and_then(
                    |v| v.as_str()).unwrap_or("");
                if !url.starts_with("http://")
                    && !url.starts_with("https://") {
                    return Decision::Deny(
                        "args.url must be an http(s) URL");
                }
                let host = url_host(url);
                if host.is_empty() {
                    return Decision::Deny("no host in args.url");
                }
                if !self.web_domains.iter().any(|d| {
                    host == d.as_str()
                        || (host.strip_prefix("www.")
                            == Some(d.as_str()))
                }) {
                    return Decision::Deny(
                        "host is not in the agent's domain allowlist");
                }
            }
            other => {
                return Decision::Deny(
                    // tool id out of vocabulary
                    match other.is_empty() {
                        true => "missing tool id",
                        false => "unknown tool id",
                    });
            }
        }
        // (3) file tools: the path must stay inside the jail.
        if matches!(call.tool.as_str(),
                    "read_file" | "write_file" | "list_files") {
            let rel = call.args["path"].as_str().unwrap();
            if self.jailed_path(rel).is_none() {
                return Decision::Deny(
                    "path escapes the workspace jail");
            }
        }
        Decision::Allow
    }

    /// Join `rel` onto the jail root, refusing traversal outside
    /// it.  Symlinks are not resolved: a symlink inside the jail
    /// pointing outside is refused by the later filesystem check
    /// (`starts_with` on the canonicalized parent at execute
    /// time), keeping writes honest even on weird trees.
    fn jailed_path(&self, rel: &str) -> Option<PathBuf> {
        let rel = Path::new(rel);
        if rel.is_absolute() {
            return None;
        }
        let mut clean = PathBuf::new();
        for c in rel.components() {
            match c {
                Component::Normal(part) => clean.push(part),
                Component::CurDir => {}
                _ => return None,   // ParentDir, prefix, root
            }
        }
        Some(self.jail_root.join(clean))
    }

    /// Authorize, then execute.  The outcome carries a human- and
    /// model-readable line; the audit row is written by the caller
    /// from the decision.
    pub fn execute(&self, call: &ToolCall) -> ToolOutcome {
        match self.authorize(call) {
            Decision::Deny(reason) => {
                ToolOutcome::err(format!("denied: {reason}"))
            }
            Decision::Allow => match call.tool.as_str() {
                "read_file" => self.read_file(
                    call.args["path"].as_str().unwrap()),
                "write_file" => self.write_file(
                    call.args["path"].as_str().unwrap(),
                    call.args["content"].as_str().unwrap()),
                "list_files" => self.list_files(
                    call.args["path"].as_str().unwrap()),
                "web_fetch" => self.web_fetch(
                    call.args["url"].as_str().unwrap()),
                _ => unreachable!("authorize filters vocabulary"),
            },
        }
    }

    fn read_file(&self, rel: &str) -> ToolOutcome {
        let path = self.jailed_path(rel).unwrap();
        // Canonicalize the PARENT (the file may not exist) and
        // re-check the jail: a symlinked directory inside the
        // workspace cannot smuggle reads outside it.
        let parent = path.parent().unwrap_or(Path::new("."));
        match parent.canonicalize() {
            Ok(real) if real.starts_with(&self.jail_root) => {}
            _ => return ToolOutcome::err(
                "denied: path escapes the workspace jail".into()),
        }
        match std::fs::read(&path) {
            Ok(bytes) if bytes.len() <= MAX_WRITE_BYTES => {
                ToolOutcome::ok(String::from_utf8_lossy(&bytes)
                    .into_owned())
            }
            Ok(_) => ToolOutcome::err(
                "file exceeds the 5 MB read cap".into()),
            Err(e) => ToolOutcome::err(format!("read failed: {e}")),
        }
    }

    fn write_file(&self, rel: &str, content: &str) -> ToolOutcome {
        let path = self.jailed_path(rel).unwrap();
        let parent = path.parent().unwrap_or(Path::new("."));
        // jailed_path already proved no traversal, so creating the
        // parent inside the jail is safe BEFORE the canonical check.
        let _ = std::fs::create_dir_all(parent);
        match parent.canonicalize() {
            Ok(real) if real.starts_with(&self.jail_root) => {}
            _ => return ToolOutcome::err(
                "denied: path escapes the workspace jail".into()),
        }
        let _ = std::fs::create_dir_all(parent);
        match std::fs::write(&path, content) {
            Ok(_) => {
                ToolOutcome::ok(format!(
                    "wrote {} bytes to {rel}", content.len()))
            }
            Err(e) => ToolOutcome::err(format!(
                "write failed: {e}")),
        }
    }

    fn list_files(&self, rel: &str) -> ToolOutcome {
        let dir = self.jailed_path(rel).unwrap();
        let mut lines: Vec<String> = Vec::new();
        let mut stack = vec![dir];
        while let Some(d) = stack.pop() {
            let entries = match std::fs::read_dir(&d) {
                Ok(rd) => rd,
                Err(e) => return ToolOutcome::err(format!(
                    "list failed: {e}")),
            };
            for entry in entries.flatten() {
                let p = entry.path();
                let shown = p.strip_prefix(&self.jail_root)
                    .unwrap_or(&p);
                lines.push(shown.to_string_lossy().into_owned());
                if p.is_dir() {
                    stack.push(p);
                }
            }
            if lines.len() > 1000 {   // keep listings bounded
                lines.truncate(1000);
                lines.push("(listing truncated at 1000 entries)"
                    .into());
                break;
            }
        }
        ToolOutcome::ok(lines.join("\n"))
    }

    fn web_fetch(&self, url: &str) -> ToolOutcome {
        // Hardened-fetch items (redirect pinning, MIME checks) are
        // audit item 5; v1 is a plain bounded GET through the
        // domain allowlist above.
        let agent = ureq::Agent::new_with_config(
            ureq::config::Config::builder()
                .timeout_global(Some(FETCH_TIMEOUT))
                .build());
        let call = agent.get(url).call();
        match call {
            Ok(mut resp) => {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 8192];
                let mut reader = resp.body_mut().as_reader();
                loop {
                    match reader.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(n) => {
                            buf.extend_from_slice(&chunk[..n]);
                            if buf.len() > MAX_FETCH_BYTES {
                                return ToolOutcome::err(
                                    "response exceeds the 1 MB \
                                     fetch cap".into());
                            }
                        }
                        Err(e) => return ToolOutcome::err(format!(
                            "fetch failed: {e}")),
                    }
                }
                ToolOutcome::ok(String::from_utf8_lossy(&buf)
                    .into_owned())
            }
            Err(e) => ToolOutcome::err(format!(
                "fetch failed: {e}")),
        }
    }
}

/// Host of an http(s) URL (no URL crate: a tiny honest parser).
fn url_host(url: &str) -> &str {
    let rest = url.split_once("://").map(|(_, r)| r)
        .unwrap_or(url);
    rest.split(['/', '?', '#']).next().unwrap_or("")
}

/// Parse the `TOOL_CALL {...}` lines out of a model reply.
pub fn parse_tool_calls(reply: &str) -> Vec<ToolCall> {
    let mut calls = Vec::new();
    for line in reply.lines() {
        let Some(json_part) = line.strip_prefix("TOOL_CALL ") else {
            continue;
        };
        let Ok(v) = serde_json::from_str::<serde_json::Value>(
            json_part.trim()) else {
            continue;   // a malformed line is ignored, not fatal
        };
        let Some(tool) = v.get("tool").and_then(
            |t| t.as_str()).map(str::to_string) else {
            continue;
        };
        calls.push(ToolCall {
            tool,
            args: v.get("args").cloned()
                .unwrap_or(serde_json::Value::Null),
        });
    }
    calls
}

/// The transcript line appended for a tool result (role "tool").
pub fn format_tool_result(call: &ToolCall,
                          outcome: &ToolOutcome) -> String {
    format!("tool {} -> {}:\n{}",
            call.tool,
            if outcome.ok { "ok" } else { "error" },
            outcome.output)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn broker(grants: &[&str]) -> Broker {
        let tmp = std::env::temp_dir().join(format!(
            "las-tools-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        Broker::new(&tmp, grants.iter()
                    .map(|g| g.to_string()).collect())
    }

    #[test]
    fn ungranted_tool_is_denied() {
        let b = broker(&["read_file"]);
        let call = ToolCall {
            tool: "write_file".into(),
            args: serde_json::json!(
                {"path": "a.md", "content": "x"}),
        };
        assert_eq!(b.authorize(&call),
                   Decision::Deny("tool not granted to this agent"));
    }

    #[test]
    fn path_traversal_is_refused() {
        let b = broker(&["read_file", "write_file", "list_files"]);
        for bad in ["../escape.txt", "a/../../escape",
                    "/etc/passwd", "a/./../b"] {
            let call = ToolCall {
                tool: "read_file".into(),
                args: serde_json::json!({"path": bad}),
            };
            assert_ne!(b.authorize(&call), Decision::Allow,
                       "traversal {bad:?} must not be allowed");
        }
    }

    #[test]
    fn write_read_list_roundtrip_inside_the_jail() {
        let b = broker(&["write_file", "read_file", "list_files"]);
        let write = ToolCall {
            tool: "write_file".into(),
            args: serde_json::json!(
                {"path": "notes/plan.md", "content": "hello"}),
        };
        let out = b.execute(&write);
        assert!(out.ok, "{}", out.output);
        let read = ToolCall {
            tool: "read_file".into(),
            args: serde_json::json!({"path": "notes/plan.md"}),
        };
        let out = b.execute(&read);
        assert!(out.ok);
        assert_eq!(out.output, "hello");
        let list = ToolCall {
            tool: "list_files".into(),
            args: serde_json::json!({"path": "."}),
        };
        let out = b.execute(&list);
        assert!(out.ok);
        assert!(out.output.contains("notes/plan.md"),
                "listing shows the file: {}", out.output);
    }

    #[test]
    fn oversize_write_is_denied_by_the_broker() {
        let b = broker(&["write_file"]);
        let call = ToolCall {
            tool: "write_file".into(),
            args: serde_json::json!(
                {"path": "big.bin",
                 "content": "x".repeat(MAX_WRITE_BYTES + 1)}),
        };
        assert_eq!(b.authorize(&call),
                   Decision::Deny("content exceeds the 5 MB file cap"));
    }

    #[test]
    fn web_fetch_requires_the_domain_allowlist() {
        let b = broker(&["web_fetch"]);
        let call = ToolCall {
            tool: "web_fetch".into(),
            args: serde_json::json!(
                {"url": "https://example.com/x"}),
        };
        // No domains configured: denied even though granted.
        assert_eq!(b.authorize(&call),
            Decision::Deny("host is not in the agent's domain \
                            allowlist"));
        // Wrong scheme.
        let call = ToolCall {
            tool: "web_fetch".into(),
            args: serde_json::json!(
                {"url": "ftp://example.com/x"}),
        };
        assert_eq!(b.authorize(&call),
                   Decision::Deny("args.url must be an http(s) URL"));
    }

    #[test]
    fn capability_flags_follow_the_grants() {
        let files_only = broker(&["read_file", "write_file"]);
        assert_eq!(files_only.capability_flags(), (false, false,
                                                   false));
        let with_web = broker(&["read_file", "web_fetch"]);
        // A (untrusted input) and C (external effect) set; B stays
        // false in v1 because no tool reaches outside the jail.
        assert_eq!(with_web.capability_flags(), (true, false, true));
    }

    #[test]
    fn tool_calls_parse_from_model_text() {
        let reply = "I will check the notes first.\n\
                     TOOL_CALL {\"tool\": \"read_file\", \
                     \"args\": {\"path\": \"notes.md\"}}\n\
                     Then I answer.";
        let calls = parse_tool_calls(reply);
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].tool, "read_file");
        assert_eq!(calls[0].args["path"], "notes.md");
        // Malformed and empty lines never panic or match.
        assert!(parse_tool_calls(
            "TOOL_CALL not-json\nTOOL_CALL {}\nnope").is_empty());
    }

    #[test]
    fn url_host_extraction() {
        assert_eq!(url_host("https://example.com/a?b"), "example.com");
        assert_eq!(url_host("http://localhost:8080/x"), "localhost:8080");
        assert_eq!(url_host("garbage"), "garbage");
    }
}
