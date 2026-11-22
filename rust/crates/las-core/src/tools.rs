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
/// Redirect hops followed (each re-validated).
pub const MAX_REDIRECTS: usize = 3;

/// Grant modes (spec 5.9 / TLS-2): Always runs, Never refuses,
/// AskOnce and AskEach defer to the interactive approval gate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GrantMode {
    Always,
    AskOnce,
    AskEach,
    Never,
}

impl GrantMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            GrantMode::Always => "always",
            GrantMode::AskOnce => "ask_once",
            GrantMode::AskEach => "ask_each",
            GrantMode::Never => "never",
        }
    }

    pub fn parse(s: &str) -> Option<GrantMode> {
        match s {
            "always" => Some(GrantMode::Always),
            "ask_once" => Some(GrantMode::AskOnce),
            "ask_each" => Some(GrantMode::AskEach),
            "never" => Some(GrantMode::Never),
            _ => None,
        }
    }
}

/// One tool grant with its mode.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolGrant {
    pub tool: String,
    pub mode: GrantMode,
}

impl ToolGrant {
    pub fn always(tool: &str) -> Self {
        Self { tool: tool.into(), mode: GrantMode::Always }
    }
}

/// Parse tool_grants_json, accepting BOTH the old format (an
/// array of tool-id strings, treated as Always) and the object
/// format ({"tool": ..., "mode": ...}).
pub fn parse_grants(json: &str) -> Vec<ToolGrant> {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(json)
        else { return Vec::new(); };
    let Some(arr) = v.as_array() else { return Vec::new() };
    arr.iter().filter_map(|e| match e {
        serde_json::Value::String(s) => Some(ToolGrant {
            tool: s.clone(), mode: GrantMode::Always }),
        serde_json::Value::Object(o) => {
            let tool = o.get("tool")?.as_str()?.to_string();
            let mode = o.get("mode").and_then(
                |m| m.as_str()).and_then(GrantMode::parse)
                .unwrap_or(GrantMode::Always);
            Some(ToolGrant { tool, mode })
        }
        _ => None,
    }).collect()
}

/// Serialize grants to the object format.
pub fn grants_to_json(grants: &[ToolGrant]) -> String {
    serde_json::Value::Array(grants.iter().map(|g|
        serde_json::json!({"tool": g.tool, "mode": g.mode.as_str()}))
        .collect()).to_string()
}

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
    /// Granted, but the mode defers to the approval gate: the
    /// caller must record a pending approval and tell the model.
    Ask(&'static str),
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
    grants: Vec<ToolGrant>,
    /// Tools with an approved one-shot (AskEach) pass: consumed
    /// once, then the tool defers again.
    one_shot: Vec<String>,
    web_domains: Vec<String>,
    /// Allow fetching private/loopback addresses (LAN endpoints,
    /// test harnesses).  OFF by default: the spec denies them
    /// (audit item 12, SSRF).
    allow_private_fetch: bool,
}

impl Broker {
    /// `jail_root` is the agent's workspace directory; file tools
    /// can never see outside it.
    pub fn new(jail_root: impl Into<PathBuf>,
               grants: Vec<ToolGrant>) -> Self {
        let jail_root = jail_root.into();
        // Canonicalize once so every later starts_with comparison
        // runs on canonical paths on both sides (symlinks like
        // /tmp -> /private/tmp would otherwise deny everything).
        let _ = std::fs::create_dir_all(&jail_root);
        let jail_root = jail_root.canonicalize()
            .unwrap_or(jail_root);
        Self { jail_root, grants, one_shot: Vec::new(),
               web_domains: Vec::new(),
               allow_private_fetch: false }
    }

    /// Tools approved for a single use (AskEach approvals).
    pub fn with_one_shot(mut self, tools: Vec<String>) -> Self {
        self.one_shot = tools;
        self
    }

    /// Explicitly allow private/loopback fetch targets (LAN
    /// endpoints, tests).  Defaults to off.
    pub fn allow_private_fetch(mut self, allow: bool) -> Self {
        self.allow_private_fetch = allow;
        self
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
        // Never and ask-gated tools cannot act without the user,
        // so only Always (or one-shot) web_fetch counts as the
        // agent reaching outside.
        let web = self.grants.iter().any(|g|
            g.tool == "web_fetch" && g.mode == GrantMode::Always)
            || self.one_shot.iter().any(|t| t == "web_fetch");
        (web, false, web)
    }

    /// Authorization in the spec 5.9 order.
    pub fn authorize(&self, call: &ToolCall) -> Decision {
        // (1) allowlist: explicit grants only, mode-aware.
        let Some(grant) = self.grants.iter().find(
            |g| g.tool == call.tool) else {
            return Decision::Deny("tool not granted to this agent");
        };
        match grant.mode {
            GrantMode::Never => {
                return Decision::Deny(
                    "grant mode for this tool is never");
            }
            GrantMode::AskOnce | GrantMode::AskEach => {
                if self.one_shot.iter().any(|t| *t == call.tool) {
                    // Approved for exactly this use.
                } else {
                    return Decision::Ask(
                        "this tool needs user approval first");
                }
            }
            GrantMode::Always => {}
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
            Decision::Ask(reason) => {
                ToolOutcome::err(format!(
                    "deferred: {reason} (an approval request was \
                     recorded; approve it on the Tools screen)"))
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

    /// Is this IP private, loopback, link-local or otherwise not
    /// a public target?  (Audit item 12: SSRF hardening.)
    fn ip_is_private(ip: std::net::IpAddr) -> bool {
        match ip {
            std::net::IpAddr::V4(v4) => {
                v4.is_private() || v4.is_loopback()
                    || v4.is_link_local() || v4.is_unspecified()
                    || v4.is_broadcast() || v4.is_documentation()
            }
            std::net::IpAddr::V6(v6) => {
                if v6.is_loopback() || v6.is_unspecified() {
                    return true;
                }
                // Unique local addresses fc00::/7.
                let seg = v6.segments()[0];
                if seg & 0xfe00 == 0xfc00 {
                    return true;
                }
                // IPv4-mapped addresses carry IPv4 privacy.
                match v6.to_ipv4_mapped() {
                    Some(v4) => Self::ip_is_private(
                        std::net::IpAddr::V4(v4)),
                    None => false,
                }
            }
        }
    }

    /// SSRF gate: every address the host resolves to must be
    /// public (unless the broker explicitly allows private
    /// targets).
    fn host_ips_are_public(&self, host: &str)
                           -> Result<(), String> {
        use std::net::ToSocketAddrs;
        let target = if host.contains(':') {
            host.to_string()   // already host:port
        } else {
            format!("{host}:80")
        };
        let addrs: Vec<_> = target.to_socket_addrs()
            .map_err(|e| format!("DNS resolution failed: {e}"))?
            .collect();
        if addrs.is_empty() {
            return Err("host resolves to no address".into());
        }
        for a in addrs {
            if Self::ip_is_private(a.ip()) {
                if !self.allow_private_fetch {
                    return Err(format!(
                        "host resolves to a private/loopback \
                         address ({}), refused", a.ip()));
                }
            }
        }
        Ok(())
    }

    fn web_fetch(&self, url: &str) -> ToolOutcome {
        // Hardened fetch (audit item 12): domain allowlist +
        // resolved-IP checks (SSRF), manual redirects that are
        // re-validated per hop, a text-only MIME gate, and the
        // 1 MB / 10 s caps.
        let agent = ureq::Agent::new_with_config(
            ureq::config::Config::builder()
                .timeout_global(Some(FETCH_TIMEOUT))
                .max_redirects(0)   // followed manually, each
                                    // hop re-validated
                .build());
        let mut current = url.to_string();
        let mut body: Vec<u8> = Vec::new();
        for _hop in 0..=MAX_REDIRECTS {
            let host = url_host(&current);
            // Per-hop revalidation: allowlist + fresh DNS/IP check.
            if !self.web_domains.iter().any(|d| {
                host == d.as_str()
                    || (host.strip_prefix("www.")
                        == Some(d.as_str()))
            }) {
                return ToolOutcome::err(
                    "host is not in the agent's domain allowlist"
                        .into());
            }
            if let Err(reason) = self.host_ips_are_public(host) {
                return ToolOutcome::err(format!(
                    "denied: {reason}"));
            }
            let resp = agent.get(&current)
                .header("User-Agent", "las-web-fetch/1")
                .call();
            return match resp {
                Ok(resp) => {
                    let status = resp.status().as_u16();
                    if (300..400).contains(&status) {
                        // redirects(0): the 3xx comes back as a
                        // normal response.  Follow it manually -
                        // the next loop iteration re-runs the
                        // allowlist and the DNS/IP checks on the
                        // NEW host.
                        let loc = resp.headers()
                            .get("Location")
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or("").to_string();
                        if loc.is_empty() {
                            return ToolOutcome::err(format!(
                                "redirect {status} without a \
                                 Location header"));
                        }
                        current = resolve_redirect(&current, &loc);
                        continue;
                    }
                    // MIME gate: only text may enter the context.
                    let ctype = resp.headers()
                        .get("Content-Type")
                        .and_then(|v| v.to_str().ok())
                        .unwrap_or("").to_lowercase();
                    let text_like = ctype.is_empty()
                        || ctype.starts_with("text/")
                        || ctype.starts_with("application/json")
                        || ctype.starts_with("application/xml")
                        || ctype.starts_with(
                            "application/xhtml+xml")
                        || ctype.starts_with(
                            "application/javascript");
                    if !text_like {
                        return ToolOutcome::err(format!(
                            "refused content-type {ctype:?} \
                             (only text is fetched)"));
                    }
                    let mut body_owned = resp.into_body();
                    let mut reader = body_owned.as_reader();
                    let mut chunk = [0u8; 8192];
                    loop {
                        match reader.read(&mut chunk) {
                            Ok(0) => break,
                            Ok(n) => {
                                body.extend_from_slice(
                                    &chunk[..n]);
                                if body.len() > MAX_FETCH_BYTES {
                                    return ToolOutcome::err(
                                        "response exceeds the \
                                         1 MB fetch cap".into());
                                }
                            }
                            Err(e) => {
                                return ToolOutcome::err(format!(
                                    "fetch failed: {e}"))
                            }
                        }
                    }
                    return ToolOutcome::ok(
                        String::from_utf8_lossy(&body)
                            .into_owned());
                }
                Err(e) => ToolOutcome::err(format!(
                    "fetch failed: {e}")),
            }
        }
        ToolOutcome::err("too many redirects (over                           MAX_REDIRECTS hops)".into())
    }
}

/// Resolve a redirect Location (absolute or path-relative)
/// against the current URL.  No URL crate: a tiny honest parser.
fn resolve_redirect(current: &str, location: &str) -> String {
    if location.starts_with("http://")
        || location.starts_with("https://") {
        return location.to_string();
    }
    // scheme://host prefix of the current URL.
    let scheme_end = current.find("://")
        .map(|i| i + 3).unwrap_or(0);
    let host_end = current[scheme_end..]
        .find(['/', '?', '#'])
        .map(|i| scheme_end + i)
        .unwrap_or(current.len());
    let base = &current[..host_end];
    if location.starts_with('/') {
        format!("{base}{location}")
    } else {
        // A relative path: resolve against the current directory.
        let dir_end = current.rfind('/').unwrap_or(
            current.len());
        format!("{}/{}", &current[..dir_end], location)
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
                    .map(|g| ToolGrant::always(g)).collect())
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
    fn ask_mode_defers_and_one_shot_passes_once() {
        let tmp = std::env::temp_dir().join(format!(
            "las-tools-ask-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        let grants = vec![
            ToolGrant { tool: "read_file".into(),
                        mode: GrantMode::AskOnce },
            ToolGrant { tool: "write_file".into(),
                        mode: GrantMode::AskEach },
            ToolGrant { tool: "list_files".into(),
                        mode: GrantMode::Never },
        ];
        let call = |t: &str| ToolCall {
            tool: t.into(),
            args: serde_json::json!({"path": "a.md"}),
        };
        let b = Broker::new(&tmp, grants.clone());
        // Ask modes defer without a one-shot.
        assert_eq!(b.authorize(&call("read_file")),
            Decision::Ask("this tool needs user approval first"));
        assert_eq!(b.authorize(&call("write_file")),
            Decision::Ask("this tool needs user approval first"));
        // Never denies even with a one-shot present.
        let b2 = Broker::new(&tmp, grants.clone())
            .with_one_shot(vec![
                "read_file".into(), "write_file".into(),
                "list_files".into()]);
        assert_eq!(b2.authorize(&call("list_files")),
            Decision::Deny("grant mode for this tool is never"));
        // With a one-shot, ask modes run - and the flags count
        // one-shot reach.
        assert_eq!(b2.authorize(&call("read_file")),
                   Decision::Allow);
    }

    #[test]
    fn grants_json_roundtrip_and_legacy_strings() {
        // Legacy string arrays parse as Always grants.
        let legacy = parse_grants(
            r#"["read_file", "web_fetch"]"#);
        assert_eq!(legacy, vec![
            ToolGrant::always("read_file"),
            ToolGrant::always("web_fetch")]);
        // Object format round-trips with modes.
        let grants = vec![
            ToolGrant::always("read_file"),
            ToolGrant { tool: "web_fetch".into(),
                        mode: GrantMode::AskOnce },
            ToolGrant { tool: "write_file".into(),
                        mode: GrantMode::AskEach },
            ToolGrant { tool: "list_files".into(),
                        mode: GrantMode::Never },
        ];
        let json = grants_to_json(&grants);
        assert_eq!(parse_grants(&json), grants);
        // Garbage degrades to no grants, never to all-granted.
        assert!(parse_grants("not json").is_empty());
        assert!(parse_grants("{}").is_empty());
    }

    #[test]
    fn ask_grants_do_not_set_capability_flags() {
        let tmp = std::env::temp_dir().join(format!(
            "las-tools-flags-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&tmp);
        let gated = vec![ToolGrant {
            tool: "web_fetch".into(),
            mode: GrantMode::AskOnce }];
        let b = Broker::new(&tmp, gated);
        // Ask-gated web_fetch cannot act without the user, so the
        // flags stay clear until a one-shot exists.
        assert_eq!(b.capability_flags(), (false, false, false));
        let b2 = Broker::new(&tmp, vec![])
            .with_one_shot(vec!["web_fetch".into()]);
        assert_eq!(b2.capability_flags(), (true, false, true));
    }

    use std::io::{Read as _, Write as _};
    use std::net::{IpAddr, TcpListener};

    /// A minimal real HTTP server on 127.0.0.1 for the hardened
    /// fetch tests (SSRF, redirects, MIME).
    fn spawn_test_server() -> std::net::SocketAddr {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let mut s = stream;
                let mut buf = [0u8; 4096];
                let mut n = 0usize;
                // Read until end of the request headers.
                loop {
                    let k = s.read(&mut buf[n..]).unwrap_or(0);
                    if k == 0 { break; }
                    n += k;
                    if String::from_utf8_lossy(&buf[..n])
                        .contains("\r\n\r\n") || n == buf.len()
                    { break; }
                }
                let req = String::from_utf8_lossy(&buf[..n])
                    .into_owned();
                let path = req.split(' ').nth(1)
                    .unwrap_or("/");
                let resp: String = match path {
                    "/ok" =>
                        "HTTP/1.1 200 OK\r\nContent-Type: \
                         text/plain\r\nContent-Length: 5\r\n\
                         Connection: close\r\n\r\nhello"
                        .into(),
                    "/redir" =>
                        "HTTP/1.1 302 Found\r\nLocation: /ok\r\n\
                         Content-Length: 0\r\nConnection: close\r\n\
                         \r\n".into(),
                    "/cross" =>
                        // Redirects to a host:port NOT in the
                        // allowlist (127.0.0.1:1).
                        "HTTP/1.1 302 Found\r\nLocation: \
                         http://127.0.0.1:1/ok\r\nContent-Length: 0\
                         \r\nConnection: close\r\n\r\n".into(),
                    "/bin" =>
                        "HTTP/1.1 200 OK\r\nContent-Type: \
                         application/octet-stream\r\n\
                         Content-Length: 4\r\nConnection: close\
                         \r\n\r\n\u{00}\u{01}\u{02}\u{03}".into(),
                    _ =>
                        "HTTP/1.1 404 Not Found\r\n\
                         Content-Length: 0\r\nConnection: close\
                         \r\n\r\n".into(),
                };
                let _ = s.write_all(resp.as_bytes());
            }
        });
        addr
    }

    #[test]
    fn private_ip_ranges_are_detected() {
        let privv = ["10.0.0.1", "172.16.5.5", "192.168.1.1",
                     "127.0.0.1", "169.254.9.9", "0.0.0.0",
                     "255.255.255.255", "203.0.113.5",
                     "::1", "fc00::1", "fd12::1",
                     "::ffff:192.168.0.1"];
        for a in privv {
            let ip: IpAddr = a.parse().unwrap();
            assert!(Broker::ip_is_private(ip), "{a} is private");
        }
        let public = ["8.8.8.8", "1.1.1.1", "2606:4700::1111"];
        for a in public {
            let ip: IpAddr = a.parse().unwrap();
            assert!(!Broker::ip_is_private(ip),
                    "{a} is public and must pass");
        }
    }

    #[test]
    fn ssrf_loopback_is_refused_by_default() {
        let addr = spawn_test_server();
        let host = format!("127.0.0.1:{}", addr.port());
        let b = broker(&["web_fetch"])
            .with_web_domains(vec![host.clone()])
            .allow_private_fetch(false);
        let out = b.execute(&ToolCall {
            tool: "web_fetch".into(),
            args: serde_json::json!(
                {"url": format!("http://{host}/ok")}),
        });
        assert!(!out.ok, "loopback must be refused by default");
        assert!(out.output.contains("private/loopback"),
                "{}", out.output);
    }

    #[test]
    fn hardened_fetch_follows_validated_redirects_and_mime() {
        let addr = spawn_test_server();
        let host = format!("127.0.0.1:{}", addr.port());
        // Tests explicitly allow the loopback target; the domain
        // allowlist and every other check still applies.
        let b = broker(&["web_fetch"])
            .with_web_domains(vec![host.clone()])
            .allow_private_fetch(true);
        let call = |path: &str| b.execute(&ToolCall {
            tool: "web_fetch".into(),
            args: serde_json::json!(
                {"url": format!("http://{host}{path}")}),
        });

        // Plain text fetch works.
        let out = call("/ok");
        assert!(out.ok, "{}", out.output);
        assert_eq!(out.output, "hello");

        // A same-host redirect is followed manually and fetched.
        let out = call("/redir");
        assert!(out.ok, "{}", out.output);
        assert_eq!(out.output, "hello");

        // A redirect to a non-allowlisted host is REFUSED (the
        // Location is re-validated per hop).
        let out = call("/cross");
        assert!(!out.ok, "{}", out.output);
        assert!(out.output.contains(
            "not in the agent's domain allowlist"),
            "{}", out.output);

        // Binary content types never enter the context.
        let out = call("/bin");
        assert!(!out.ok, "{}", out.output);
        assert!(out.output.contains("content-type"),
                "{}", out.output);
    }

    #[test]
    fn redirect_locations_resolve_relative_and_absolute() {
        assert_eq!(
            resolve_redirect("http://a.example/x/y", "/z"),
            "http://a.example/z");
        assert_eq!(
            resolve_redirect("http://a.example/x/y", "z"),
            "http://a.example/x/z");
        assert_eq!(
            resolve_redirect("http://a.example/x", "https://b/x"),
            "https://b/x");
    }

    #[test]
    fn url_host_extraction() {
        assert_eq!(url_host("https://example.com/a?b"), "example.com");
        assert_eq!(url_host("http://localhost:8080/x"), "localhost:8080");
        assert_eq!(url_host("garbage"), "garbage");
    }
}
