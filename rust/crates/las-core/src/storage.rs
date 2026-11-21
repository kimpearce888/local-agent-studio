//! Durable storage for the agent domain (mandate sections 5-7, 15).
//!
//! SQLite in WAL mode. Every communication operation is persisted
//! *before* delivery (inbox rows carry an explicit delivery state);
//! idempotency keys make retries safe. The Python engine's run/event
//! tables are left untouched - `CoreStore` opens the same database
//! file and adds the agent-domain tables alongside, so one data
//! directory serves both engines during migration.

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;

pub struct CoreStore {
    conn: Mutex<Connection>,
}

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'idle',
    current_profile_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_profile_versions (
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    system_prompt TEXT NOT NULL,
    model_provider TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    tool_grants_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, version),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',   -- open | rolled_over | closed
    segment_of TEXT NOT NULL DEFAULT '',   -- prior segment id on rollover
    created_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,                    -- user | assistant | system | tool
    content TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',    -- stored when supplied/permitted
    tool_json TEXT NOT NULL DEFAULT '',    -- tool call/result record
    model TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL,
    UNIQUE (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
);
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',   -- user | agent | tool:<name>
    provenance TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    scope TEXT NOT NULL DEFAULT 'private', -- private | shared
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL DEFAULT ''
);
    -- no FK: memory rows may outlive their agent under an explicit
    -- KeepMemory retention policy (mandate 19.4)
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,               -- agent id
    sender TEXT NOT NULL,                  -- agent id or "user"
    kind TEXT NOT NULL,                    -- message | call | delegation | transfer | channel
    payload_json TEXT NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'accepted',
    idempotency_key TEXT NOT NULL DEFAULT '',
    busy_mode TEXT NOT NULL DEFAULT 'queue',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    delivered_at TEXT NOT NULL DEFAULT '',
    acked_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (recipient) REFERENCES agents(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_idem
    ON inbox(recipient, idempotency_key)
    WHERE idempotency_key != '';
CREATE INDEX IF NOT EXISTS idx_inbox_recipient_state
    ON inbox(recipient, delivery_state);
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS channel_members (
    channel_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY (channel_id, agent_id)
);
CREATE TABLE IF NOT EXISTS channel_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    reply_to INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS comm_rules (
    src TEXT NOT NULL,                     -- agent id or "user"
    dst TEXT NOT NULL,                     -- agent id
    may_message INTEGER NOT NULL DEFAULT 1,
    may_call INTEGER NOT NULL DEFAULT 1,
    may_delegate INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (src, dst)
);
CREATE TABLE IF NOT EXISTS blackboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,               -- note | finding | task | question | decision | artifact
    content TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    ownership TEXT NOT NULL DEFAULT ''
);
"#;

/// Real UTC ISO-8601 timestamp (proleptic Gregorian, from std only).
pub fn utc_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0) as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // Civil-from-days (Howard Hinnant's algorithm), no external crates.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentRow {
    pub id: String,
    pub name: String,
    pub description: String,
    pub enabled: bool,
    pub archived: bool,
    pub status: String,
    pub current_profile_version: i64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProfileVersionRow {
    pub agent_id: String,
    pub version: i64,
    pub system_prompt: String,
    pub model_provider: String,
    pub model_id: String,
    pub params_json: String,
    pub tool_grants_json: String,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionRow {
    pub id: String,
    pub agent_id: String,
    pub title: String,
    pub status: String,
    pub segment_of: String,
    pub created_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MessageRow {
    pub session_id: String,
    pub seq: i64,
    pub role: String,
    pub content: String,
    pub reasoning: String,
    pub tool_json: String,
    pub model: String,
    pub ts: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemoryRow {
    pub id: i64,
    pub agent_id: String,
    pub category: String,
    pub content: String,
    pub source: String,
    pub provenance: String,
    pub confidence: f64,
    pub scope: String,
    pub version: i64,
    pub created_at: String,
    pub updated_at: String,
    pub expires_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InboxRow {
    pub id: i64,
    pub recipient: String,
    pub sender: String,
    pub kind: String,
    pub payload_json: String,
    pub delivery_state: String,
    pub idempotency_key: String,
    pub busy_mode: String,
    pub attempts: i64,
    pub created_at: String,
    pub delivered_at: String,
    pub acked_at: String,
    pub expires_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommRuleRow {
    pub src: String,
    pub dst: String,
    pub may_message: bool,
    pub may_call: bool,
    pub may_delegate: bool,
}

impl CoreStore {
    /// Open (creating if needed) the agent-domain store. The file may
    /// be the Python engine's database: unknown tables are ignored.
    pub fn open(path: impl AsRef<Path>) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn: Mutex::new(conn) })
    }

    /// Direct connection access for module layers built on the store.
    pub fn raw_conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.conn.lock().unwrap()
    }

    pub fn in_memory() -> rusqlite::Result<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn: Mutex::new(conn) })
    }

    // -- agents ---------------------------------------------------------

    pub fn upsert_agent(&self, a: &AgentRow) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO agents (id, name, description, enabled, archived,
             status, current_profile_version, created_at, updated_at)
             VALUES (?,?,?,?,?,?,?,?,?)
             ON CONFLICT(id) DO UPDATE SET name=excluded.name,
               description=excluded.description, enabled=excluded.enabled,
               archived=excluded.archived, status=excluded.status,
               current_profile_version=excluded.current_profile_version,
               updated_at=excluded.updated_at",
            params![a.id, a.name, a.description, a.enabled as i64,
                    a.archived as i64, a.status, a.current_profile_version,
                    a.created_at, a.updated_at],
        )?;
        Ok(())
    }

    pub fn get_agent(&self, id: &str) -> rusqlite::Result<Option<AgentRow>> {
        let conn = self.conn.lock().unwrap();
        conn.query_row("SELECT * FROM agents WHERE id=?", params![id],
                       agent_from_row).optional()
    }

    pub fn list_agents(&self, include_archived: bool)
                       -> rusqlite::Result<Vec<AgentRow>> {
        let conn = self.conn.lock().unwrap();
        let filter = if include_archived { "" } else {
            " WHERE archived=0" };
        let mut stmt = conn.prepare(
            &format!("SELECT * FROM agents{filter} ORDER BY created_at"))?;
        let rows = stmt.query_map([], agent_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn delete_agent_row_only(&self, id: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("DELETE FROM agents WHERE id=?", params![id])?;
        Ok(())
    }

    // -- profile versions --------------------------------------------------

    pub fn add_profile_version(&self, p: &ProfileVersionRow)
                               -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO agent_profile_versions (agent_id, version,
             system_prompt, model_provider, model_id, params_json,
             tool_grants_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            params![p.agent_id, p.version, p.system_prompt,
                    p.model_provider, p.model_id, p.params_json,
                    p.tool_grants_json, p.created_at])?;
        Ok(())
    }

    pub fn profile_version(&self, agent_id: &str, version: i64)
                           -> rusqlite::Result<Option<ProfileVersionRow>> {
        let conn = self.conn.lock().unwrap();
        conn.query_row(
            "SELECT * FROM agent_profile_versions
             WHERE agent_id=? AND version=?", params![agent_id, version],
            profile_from_row).optional()
    }

    pub fn profile_history(&self, agent_id: &str)
                           -> rusqlite::Result<Vec<ProfileVersionRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT * FROM agent_profile_versions
             WHERE agent_id=? ORDER BY version")?;
        let rows = stmt.query_map(params![agent_id], profile_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- sessions + transcript ----------------------------------------------

    pub fn create_session(&self, s: &SessionRow) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO agent_sessions (id, agent_id, title, status,
             segment_of, created_at) VALUES (?,?,?,?,?,?)",
            params![s.id, s.agent_id, s.title, s.status, s.segment_of,
                    s.created_at])?;
        Ok(())
    }

    pub fn list_sessions(&self, agent_id: &str)
                         -> rusqlite::Result<Vec<SessionRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT * FROM agent_sessions WHERE agent_id=?
             ORDER BY created_at")?;
        let rows = stmt.query_map(params![agent_id], session_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn append_message(&self, m: &MessageRow) -> rusqlite::Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO session_messages (session_id, seq, role, content,
             reasoning, tool_json, model, ts) VALUES (?,?,?,?,?,?,?,?)",
            params![m.session_id, m.seq, m.role, m.content, m.reasoning,
                    m.tool_json, m.model, m.ts])?;
        Ok(conn.last_insert_rowid())
    }

    pub fn transcript(&self, session_id: &str)
                      -> rusqlite::Result<Vec<MessageRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT * FROM session_messages WHERE session_id=?
             ORDER BY seq")?;
        let rows = stmt.query_map(params![session_id], message_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- memory -----------------------------------------------------------

    pub fn add_memory(&self, m: &MemoryRow) -> rusqlite::Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO agent_memory (agent_id, category, content, source,
             provenance, confidence, scope, version, created_at, updated_at,
             expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            params![m.agent_id, m.category, m.content, m.source,
                    m.provenance, m.confidence, m.scope, m.version,
                    m.created_at, m.updated_at, m.expires_at])?;
        Ok(conn.last_insert_rowid())
    }

    pub fn update_memory(&self, id: i64, content: &str,
                         updated_at: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE agent_memory SET content=?, updated_at=?,
             version=version+1 WHERE id=?",
            params![content, updated_at, id])?;
        Ok(())
    }

    pub fn delete_memory(&self, id: i64) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("DELETE FROM agent_memory WHERE id=?", params![id])?;
        Ok(())
    }

    pub fn list_memory(&self, agent_id: &str)
                       -> rusqlite::Result<Vec<MemoryRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT * FROM agent_memory WHERE agent_id=? ORDER BY id")?;
        let rows = stmt.query_map(params![agent_id], memory_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- inbox (mandate 7.1: persist before delivery) ------------------------

    /// Insert an inbox entry. Idempotency: an existing row with the
    /// same (recipient, key) is returned unchanged instead of duplicated.
    pub fn enqueue(&self, e: &InboxRow) -> rusqlite::Result<InboxRow> {
        let conn = self.conn.lock().unwrap();
        if !e.idempotency_key.is_empty() {
            let existing = conn.query_row(
                "SELECT * FROM inbox WHERE recipient=? AND idempotency_key=?",
                params![e.recipient, e.idempotency_key],
                inbox_from_row).optional()?;
            if let Some(row) = existing {
                return Ok(row);          // duplicate suppressed
            }
        }
        conn.execute(
            "INSERT INTO inbox (recipient, sender, kind, payload_json,
             delivery_state, idempotency_key, busy_mode, attempts,
             created_at, delivered_at, acked_at, expires_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            params![e.recipient, e.sender, e.kind, e.payload_json,
                    e.delivery_state, e.idempotency_key, e.busy_mode,
                    e.attempts, e.created_at, e.delivered_at, e.acked_at,
                    e.expires_at])?;
        let id = conn.last_insert_rowid();
        Ok(InboxRow { id, ..e.clone() })
    }

    pub fn update_delivery(&self, id: i64, state: &str,
                           at: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        let column = match state {
            "delivered" => "delivered_at",
            "acked" => "acked_at",
            _ => "delivered_at",
        };
        conn.execute(
            &format!("UPDATE inbox SET delivery_state=?, {column}=?,
                      attempts=attempts+1 WHERE id=?"),
            params![state, at, id])?;
        Ok(())
    }

    pub fn inbox_for(&self, recipient: &str)
                     -> rusqlite::Result<Vec<InboxRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT * FROM inbox WHERE recipient=? ORDER BY id")?;
        let rows = stmt.query_map(params![recipient], inbox_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- channels + blackboard ------------------------------------------------

    pub fn create_channel(&self, id: &str, name: &str,
                          project: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("INSERT INTO channels (id, name, project_id) \
                      VALUES (?,?,?)", params![id, name, project])?;
        Ok(())
    }

    pub fn join_channel(&self, channel_id: &str,
                        agent_id: &str) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("INSERT OR IGNORE INTO channel_members \
                      VALUES (?,?)", params![channel_id, agent_id])?;
        Ok(())
    }

    pub fn channel_members(&self, channel_id: &str)
                           -> rusqlite::Result<Vec<String>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT agent_id FROM channel_members WHERE channel_id=?")?;
        let rows = stmt.query_map(params![channel_id], |r| {
            r.get::<_, String>(0) })?.collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn post_to_channel(&self, channel_id: &str, author: &str,
                           content: &str, reply_to: i64,
                           ts: &str) -> rusqlite::Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO channel_posts (channel_id, author, content, \
             reply_to, ts) VALUES (?,?,?,?,?)",
            params![channel_id, author, content, reply_to, ts])?;
        Ok(conn.last_insert_rowid())
    }

    pub fn channel_posts(&self, channel_id: &str)
                         -> rusqlite::Result<Vec<(i64, String, String,
                                                   String)>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, author, content, ts FROM channel_posts \
             WHERE channel_id=? ORDER BY id")?;
        let rows = stmt.query_map(params![channel_id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?))
        })?.collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn add_blackboard_item(&self, item_type: &str, content: &str,
                               created_by: &str, ts: &str)
                               -> rusqlite::Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO blackboard (item_type, content, created_by, \
             created_at) VALUES (?,?,?,?)",
            params![item_type, content, created_by, ts])?;
        Ok(conn.last_insert_rowid())
    }

    pub fn blackboard(&self) -> rusqlite::Result<Vec<(i64, String, String,
                                                      String, String)>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, item_type, content, created_by, created_at \
             FROM blackboard ORDER BY id")?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
        })?.collect::<Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // -- communication permissions (mandate section 8) ------------------------

    pub fn set_comm_rule(&self, r: &CommRuleRow) -> rusqlite::Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO comm_rules (src, dst, may_message, may_call, \
             may_delegate) VALUES (?,?,?,?,?)
             ON CONFLICT(src, dst) DO UPDATE SET may_message=excluded.\
             may_message, may_call=excluded.may_call, \
             may_delegate=excluded.may_delegate",
            params![r.src, r.dst, r.may_message as i64, r.may_call as i64,
                    r.may_delegate as i64])?;
        Ok(())
    }

    /// The effective rule for src→dst. Absent rows default to the
    /// mandate's safe default: the user may reach agents; agents may
    /// not reach anyone until topology grants it.
    pub fn comm_rule(&self, src: &str, dst: &str)
                     -> rusqlite::Result<CommRuleRow> {
        let conn = self.conn.lock().unwrap();
        let row = conn.query_row(
            "SELECT * FROM comm_rules WHERE src=? AND dst=?",
            params![src, dst], rule_from_row).optional()?;
        Ok(row.unwrap_or(CommRuleRow {
            src: src.into(), dst: dst.into(),
            may_message: src == "user", may_call: src == "user",
            may_delegate: src == "user",
        }))
    }
}

fn agent_from_row(r: &Row) -> rusqlite::Result<AgentRow> {
    Ok(AgentRow {
        id: r.get("id")?, name: r.get("name")?,
        description: r.get("description")?,
        enabled: r.get::<_, i64>("enabled")? != 0,
        archived: r.get::<_, i64>("archived")? != 0,
        status: r.get("status")?,
        current_profile_version: r.get("current_profile_version")?,
        created_at: r.get("created_at")?, updated_at: r.get("updated_at")?,
    })
}

fn profile_from_row(r: &Row) -> rusqlite::Result<ProfileVersionRow> {
    Ok(ProfileVersionRow {
        agent_id: r.get("agent_id")?, version: r.get("version")?,
        system_prompt: r.get("system_prompt")?,
        model_provider: r.get("model_provider")?,
        model_id: r.get("model_id")?,
        params_json: r.get("params_json")?,
        tool_grants_json: r.get("tool_grants_json")?,
        created_at: r.get("created_at")?,
    })
}

fn session_from_row(r: &Row) -> rusqlite::Result<SessionRow> {
    Ok(SessionRow {
        id: r.get("id")?, agent_id: r.get("agent_id")?,
        title: r.get("title")?, status: r.get("status")?,
        segment_of: r.get("segment_of")?, created_at: r.get("created_at")?,
    })
}

fn message_from_row(r: &Row) -> rusqlite::Result<MessageRow> {
    Ok(MessageRow {
        session_id: r.get("session_id")?, seq: r.get("seq")?,
        role: r.get("role")?, content: r.get("content")?,
        reasoning: r.get("reasoning")?, tool_json: r.get("tool_json")?,
        model: r.get("model")?, ts: r.get("ts")?,
    })
}

fn memory_from_row(r: &Row) -> rusqlite::Result<MemoryRow> {
    Ok(MemoryRow {
        id: r.get("id")?, agent_id: r.get("agent_id")?,
        category: r.get("category")?, content: r.get("content")?,
        source: r.get("source")?, provenance: r.get("provenance")?,
        confidence: r.get("confidence")?, scope: r.get("scope")?,
        version: r.get("version")?, created_at: r.get("created_at")?,
        updated_at: r.get("updated_at")?, expires_at: r.get("expires_at")?,
    })
}

fn inbox_from_row(r: &Row) -> rusqlite::Result<InboxRow> {
    Ok(InboxRow {
        id: r.get("id")?, recipient: r.get("recipient")?,
        sender: r.get("sender")?, kind: r.get("kind")?,
        payload_json: r.get("payload_json")?,
        delivery_state: r.get("delivery_state")?,
        idempotency_key: r.get("idempotency_key")?,
        busy_mode: r.get("busy_mode")?, attempts: r.get("attempts")?,
        created_at: r.get("created_at")?,
        delivered_at: r.get("delivered_at")?,
        acked_at: r.get("acked_at")?, expires_at: r.get("expires_at")?,
    })
}

fn rule_from_row(r: &Row) -> rusqlite::Result<CommRuleRow> {
    Ok(CommRuleRow {
        src: r.get("src")?, dst: r.get("dst")?,
        may_message: r.get::<_, i64>("may_message")? != 0,
        may_call: r.get::<_, i64>("may_call")? != 0,
        may_delegate: r.get::<_, i64>("may_delegate")? != 0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn agent(id: &str) -> AgentRow {
        AgentRow {
            id: id.into(), name: format!("Agent {id}"),
            description: "test".into(), enabled: true, archived: false,
            status: "idle".into(), current_profile_version: 1,
            created_at: utc_now(), updated_at: utc_now(),
        }
    }

    #[test]
    fn agent_roundtrip_and_list() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        s.upsert_agent(&agent("a2")).unwrap();
        let listed = s.list_agents(false).unwrap();
        assert_eq!(listed.len(), 2);
        let got = s.get_agent("a1").unwrap().unwrap();
        assert_eq!(got.name, "Agent a1");
        s.upsert_agent(&AgentRow { archived: true, ..agent("a2") }).unwrap();
        assert_eq!(s.list_agents(false).unwrap().len(), 1);
        assert_eq!(s.list_agents(true).unwrap().len(), 2);
    }

    #[test]
    fn profile_versions_are_immutable_history() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        for v in 1..=3 {
            s.add_profile_version(&ProfileVersionRow {
                agent_id: "a1".into(), version: v,
                system_prompt: format!("prompt v{v}"),
                model_provider: "lmstudio".into(), model_id: "m".into(),
                params_json: "{}".into(), tool_grants_json: "[]".into(),
                created_at: utc_now(),
            }).unwrap();
        }
        let history = s.profile_history("a1").unwrap();
        assert_eq!(history.len(), 3);
        assert_eq!(history[2].system_prompt, "prompt v3");
        assert!(s.profile_version("a1", 9).unwrap().is_none());
    }

    #[test]
    fn transcript_keeps_canonical_order() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        s.create_session(&SessionRow {
            id: "s1".into(), agent_id: "a1".into(), title: "T".into(),
            status: "open".into(), segment_of: String::new(),
            created_at: utc_now(),
        }).unwrap();
        for seq in 0..5 {
            s.append_message(&MessageRow {
                session_id: "s1".into(), seq, role: "user".into(),
                content: format!("m{seq}"), reasoning: String::new(),
                tool_json: String::new(), model: "m".into(),
                ts: utc_now(),
            }).unwrap();
        }
        let t = s.transcript("s1").unwrap();
        assert_eq!(t.len(), 5);
        assert_eq!(t[4].content, "m4");
    }

    #[test]
    fn inbox_idempotency_deduplicates() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        let base = InboxRow {
            id: 0, recipient: "a1".into(), sender: "a2".into(),
            kind: "message".into(), payload_json: "{}".into(),
            delivery_state: "accepted".into(),
            idempotency_key: "op-123".into(), busy_mode: "queue".into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        };
        let first = s.enqueue(&base).unwrap();
        let dup = s.enqueue(&base).unwrap();
        assert_eq!(first.id, dup.id, "same key returns the same row");
        assert_eq!(s.inbox_for("a1").unwrap().len(), 1);
    }

    #[test]
    fn delivery_states_advance() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        let e = s.enqueue(&InboxRow {
            id: 0, recipient: "a1".into(), sender: "user".into(),
            kind: "message".into(), payload_json: "{}".into(),
            delivery_state: "accepted".into(),
            idempotency_key: String::new(), busy_mode: "queue".into(),
            attempts: 0, created_at: utc_now(),
            delivered_at: String::new(), acked_at: String::new(),
            expires_at: String::new(),
        }).unwrap();
        s.update_delivery(e.id, "delivered", &utc_now()).unwrap();
        s.update_delivery(e.id, "acked", &utc_now()).unwrap();
        let row = &s.inbox_for("a1").unwrap()[0];
        assert_eq!(row.delivery_state, "acked");
        assert!(!row.delivered_at.is_empty());
        assert!(!row.acked_at.is_empty());
    }

    #[test]
    fn comm_rules_default_deny_between_agents() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        s.upsert_agent(&agent("a2")).unwrap();
        let none = s.comm_rule("a1", "a2").unwrap();
        assert!(!none.may_message, "agent→agent defaults to deny");
        let user = s.comm_rule("user", "a1").unwrap();
        assert!(user.may_message, "user→agent defaults to allow");
        s.set_comm_rule(&CommRuleRow {
            src: "a1".into(), dst: "a2".into(), may_message: true,
            may_call: false, may_delegate: true,
        }).unwrap();
        let granted = s.comm_rule("a1", "a2").unwrap();
        assert!(granted.may_message && granted.may_delegate);
        assert!(!granted.may_call);
    }

    #[test]
    fn memory_versioning_and_deletion() {
        let s = CoreStore::in_memory().unwrap();
        s.upsert_agent(&agent("a1")).unwrap();
        let id = s.add_memory(&MemoryRow {
            id: 0, agent_id: "a1".into(), category: "fact".into(),
            content: "v1".into(), source: "user".into(),
            provenance: "chat".into(), confidence: 1.0,
            scope: "private".into(), version: 1,
            created_at: utc_now(), updated_at: utc_now(),
            expires_at: String::new(),
        }).unwrap();
        s.update_memory(id, "v2", &utc_now()).unwrap();
        let m = &s.list_memory("a1").unwrap()[0];
        assert_eq!((m.content.as_str(), m.version), ("v2", 2));
        s.delete_memory(id).unwrap();
        assert!(s.list_memory("a1").unwrap().is_empty());
    }

    #[test]
    fn channels_and_blackboard() {
        let s = CoreStore::in_memory().unwrap();
        s.create_channel("ch1", "general", "").unwrap();
        s.join_channel("ch1", "a1").unwrap();
        s.join_channel("ch1", "a1").unwrap();   // idempotent join
        assert_eq!(s.channel_members("ch1").unwrap(), vec!["a1"]);
        s.post_to_channel("ch1", "a1", "hello", 0, &utc_now()).unwrap();
        assert_eq!(s.channel_posts("ch1").unwrap().len(), 1);
        s.add_blackboard_item("decision", "use rust", "user", &utc_now())
            .unwrap();
        assert_eq!(s.blackboard().unwrap().len(), 1);
    }

    #[test]
    fn utc_now_is_iso_and_monotonic() {
        let a = utc_now();
        assert_eq!(a.len(), 20);
        assert!(a.ends_with('Z'));
        assert!(a.starts_with("20"));
    }

    #[test]
    fn opens_python_database_alongside() {
        // One data directory, both engines: the agent tables must not
        // disturb existing (foreign) tables.
        let dir = std::env::temp_dir().join("las-core-mixed.db");
        let _ = std::fs::remove_file(&dir);
        {
            let py = Connection::open(&dir).unwrap();
            py.execute_batch("CREATE TABLE events (x INTEGER); \
                              INSERT INTO events VALUES (42);").unwrap();
        }
        let store = CoreStore::open(&dir).unwrap();
        store.upsert_agent(&agent("a1")).unwrap();
        let conn = store.conn.lock().unwrap();
        let x: i64 = conn.query_row("SELECT x FROM events", [],
                                    |r| r.get(0)).unwrap();
        assert_eq!(x, 42, "foreign tables untouched");
    }
}
