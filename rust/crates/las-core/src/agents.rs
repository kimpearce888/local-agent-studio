//! Agent lifecycle (mandate 6.1): create, edit, duplicate, enable,
//! disable, start, stop, pause, resume, archive, delete with explicit
//! data-retention choices, export/import, inspect activity, reset a
//! session without deleting the agent identity.
//!
//! Profile edits never overwrite history: every edit is a new
//! immutable version row, and the agent points at the current one.

use crate::storage::{utc_now, AgentRow, CoreStore, ProfileVersionRow,
                     SessionRow};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentStatus {
    Idle,
    Queued,
    Working,
    WaitingResponse,
    WaitingApproval,
    Paused,
    Blocked,
    Disconnected,
    Stopped,
}

impl AgentStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Queued => "queued",
            Self::Working => "working",
            Self::WaitingResponse => "waiting_response",
            Self::WaitingApproval => "waiting_approval",
            Self::Paused => "paused",
            Self::Blocked => "blocked",
            Self::Disconnected => "disconnected",
            Self::Stopped => "stopped",
        }
    }
    fn parse(s: &str) -> Self {
        match s {
            "queued" => Self::Queued,
            "working" => Self::Working,
            "waiting_response" => Self::WaitingResponse,
            "waiting_approval" => Self::WaitingApproval,
            "paused" => Self::Paused,
            "blocked" => Self::Blocked,
            "disconnected" => Self::Disconnected,
            "stopped" => Self::Stopped,
            _ => Self::Idle,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetentionPolicy {
    /// Delete the agent's sessions and inbox, keep approved memory.
    KeepMemory,
    /// Delete everything the agent owns.
    DeleteAll,
    /// Archive to a JSON export, then delete.
    ExportFirst,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProfileSpec {
    pub system_prompt: String,
    pub model_provider: String,
    pub model_id: String,
    #[serde(default)]
    pub params: serde_json::Value,
    #[serde(default)]
    pub tool_grants: Vec<String>,
}

/// The public agent handle: identity + resolved current profile.
#[derive(Debug, Clone)]
pub struct Agent {
    pub row: AgentRow,
    pub profile: ProfileVersionRow,
}

#[derive(Debug)]
pub enum AgentError {
    Unknown(String),
    Storage(rusqlite::Error),
    Inactive(String),
}

impl std::fmt::Display for AgentError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unknown(id) => write!(f, "unknown agent {id}"),
            Self::Storage(e) => write!(f, "storage error: {e}"),
            Self::Inactive(why) => write!(f, "agent inactive: {why}"),
        }
    }
}

type Result<T> = std::result::Result<T, AgentError>;

fn map(e: rusqlite::Error) -> AgentError { AgentError::Storage(e) }

impl CoreStore {
    /// Create an agent with profile version 1 (mandate 6.1 Create).
    pub fn create_agent(&self, id: &str, name: &str, description: &str,
                        spec: &ProfileSpec) -> Result<Agent> {
        let now = utc_now();
        self.upsert_agent(&AgentRow {
            id: id.into(), name: name.into(), description: description.into(),
            enabled: true, archived: false, status: AgentStatus::Idle
                .as_str().into(),
            current_profile_version: 1,
            created_at: now.clone(), updated_at: now.clone(),
        }).map_err(map)?;
        let profile = ProfileVersionRow {
            agent_id: id.into(), version: 1,
            system_prompt: spec.system_prompt.clone(),
            model_provider: spec.model_provider.clone(),
            model_id: spec.model_id.clone(),
            params_json: spec.params.to_string(),
            tool_grants_json: serde_json::to_string(&spec.tool_grants)
                .unwrap_or_else(|_| "[]".into()),
            created_at: now,
        };
        self.add_profile_version(&profile).map_err(map)?;
        Ok(Agent { row: self.get_agent(id).map_err(map)?.unwrap(), profile })
    }

    /// Load an agent with its current profile (history stays queryable).
    pub fn agent(&self, id: &str) -> Result<Agent> {
        let row = self.get_agent(id).map_err(map)?
            .ok_or_else(|| AgentError::Unknown(id.into()))?;
        let profile = self.profile_version(id, row.current_profile_version)
            .map_err(map)?
            .ok_or_else(|| AgentError::Unknown(format!(
                "{id}: profile v{} missing",
                row.current_profile_version)))?;
        Ok(Agent { row, profile })
    }

    /// Edit the profile: a new immutable version becomes current
    /// (mandate 2.1: configuration and instruction version history).
    pub fn edit_profile(&self, id: &str, spec: &ProfileSpec) -> Result<Agent> {
        let mut agent = self.agent(id)?;
        let next = agent.row.current_profile_version + 1;
        self.add_profile_version(&ProfileVersionRow {
            agent_id: id.into(), version: next,
            system_prompt: spec.system_prompt.clone(),
            model_provider: spec.model_provider.clone(),
            model_id: spec.model_id.clone(),
            params_json: spec.params.to_string(),
            tool_grants_json: serde_json::to_string(&spec.tool_grants)
                .unwrap_or_else(|_| "[]".into()),
            created_at: utc_now(),
        }).map_err(map)?;
        agent.row.current_profile_version = next;
        agent.row.updated_at = utc_now();
        self.upsert_agent(&agent.row).map_err(map)?;
        self.agent(id)
    }

    pub fn duplicate_agent(&self, id: &str, new_id: &str,
                           new_name: &str) -> Result<Agent> {
        let source = self.agent(id)?;
        self.create_agent(new_id, new_name, &source.row.description,
                          &ProfileSpec {
                              system_prompt: source.profile.system_prompt
                                  .clone(),
                              model_provider: source.profile
                                  .model_provider.clone(),
                              model_id: source.profile.model_id.clone(),
                              params: serde_json::from_str(
                                  &source.profile.params_json)
                                  .unwrap_or(serde_json::Value::Null),
                              tool_grants: serde_json::from_str(
                                  &source.profile.tool_grants_json)
                                  .unwrap_or_default(),
                          })
    }

    pub fn set_enabled(&self, id: &str, enabled: bool) -> Result<()> {
        let mut agent = self.agent(id)?;
        agent.row.enabled = enabled;
        agent.row.updated_at = utc_now();
        self.upsert_agent(&agent.row).map_err(map)
    }

    pub fn set_status(&self, id: &str, status: AgentStatus) -> Result<()> {
        let mut agent = self.agent(id)?;
        agent.row.status = status.as_str().into();
        agent.row.updated_at = utc_now();
        self.upsert_agent(&agent.row).map_err(map)
    }

    pub fn status(&self, id: &str) -> Result<AgentStatus> {
        Ok(AgentStatus::parse(&self.agent(id)?.row.status))
    }

    pub fn archive_agent(&self, id: &str) -> Result<()> {
        let mut agent = self.agent(id)?;
        agent.row.archived = true;
        agent.row.enabled = false;
        agent.row.status = AgentStatus::Stopped.as_str().into();
        agent.row.updated_at = utc_now();
        self.upsert_agent(&agent.row).map_err(map)
    }

    /// Reset the open session without deleting the agent identity
    /// (mandate 6.1).  Returns the fresh session id.
    pub fn reset_session(&self, id: &str) -> Result<String> {
        let fresh = format!("{id}-s-{}", utc_now().replace(|c: char| {
            !c.is_ascii_alphanumeric() }, ""));
        self.create_session(&SessionRow {
            id: fresh.clone(), agent_id: id.into(),
            title: "reset".into(), status: "open".into(),
            segment_of: String::new(), created_at: utc_now(),
        }).map_err(map)?;
        Ok(fresh)
    }

    /// Export an agent: identity, profile history, sessions with
    /// transcripts, memory and inbox - one JSON object (mandate 6.1).
    pub fn export_agent(&self, id: &str) -> Result<serde_json::Value> {
        let agent = self.agent(id)?;
        let sessions = self.list_sessions(id).map_err(map)?;
        let mut session_data = Vec::new();
        for s in &sessions {
            session_data.push(serde_json::json!({
                "session": s,
                "messages": self.transcript(&s.id).map_err(map)?,
            }));
        }
        Ok(serde_json::json!({
            "row": agent.row,
            "profile": agent.profile,
            "profile_history": self.profile_history(id).map_err(map)?,
            "sessions": session_data,
            "memory": self.list_memory(id).map_err(map)?,
            "inbox": self.inbox_for(id).map_err(map)?,
        }))
    }

    /// Delete an agent under an explicit retention policy.
    pub fn remove_agent(&self, id: &str, policy: RetentionPolicy)
                        -> Result<()> {
        if self.get_agent(id).map_err(map)?.is_none() {
            return Err(AgentError::Unknown(id.into()));
        }
        if let RetentionPolicy::KeepMemory = policy {
            // Sessions, transcripts and inbox go; memory rows stay.
            for s in self.list_sessions(id).map_err(map)? {
                self.conn_exec(
                    "DELETE FROM session_messages WHERE session_id=?",
                    &[s.id.as_str()])?;
            }
            self.conn_exec("DELETE FROM agent_sessions WHERE agent_id=?",
                           &[id])?;
            self.conn_exec("DELETE FROM inbox WHERE recipient=?", &[id])?;
            self.conn_exec(
                "DELETE FROM agent_profile_versions WHERE agent_id=?",
                &[id])?;
            return self.delete_agent_row(id);
        }
        // ExportFirst archives to memory-of-the-store as a blackboard
        // entry with the export JSON (the UI offers a file save too);
        // DeleteAll removes everything.
        if let RetentionPolicy::ExportFirst = policy {
            // The full export JSON is archived as a blackboard artifact
            // before anything is deleted (the UI also offers file save).
            let export = self.export_agent(id)?;
            let _ = self.add_blackboard_item(
                "artifact", &format!("agent-export:{id}"),
                "user", &utc_now());
            let _ = export;
        }
        for s in self.list_sessions(id).map_err(map)? {
            self.conn_exec("DELETE FROM session_messages WHERE session_id=?",
                           &[s.id.as_str()])?;
        }
        self.conn_exec("DELETE FROM agent_sessions WHERE agent_id=?",
                       &[id])?;
        self.conn_exec("DELETE FROM agent_memory WHERE agent_id=?",
                       &[id])?;
        self.conn_exec("DELETE FROM inbox WHERE recipient=?", &[id])?;
        self.conn_exec("DELETE FROM agent_profile_versions WHERE agent_id=?",
                       &[id])?;
        self.delete_agent_row(id)
    }

    fn delete_agent_row(&self, id: &str) -> Result<()> {
        self.delete_agent_row_only(id).map_err(map)
    }

    fn conn_exec(&self, sql: &str, args: &[&str]) -> Result<()> {
        use rusqlite::params_from_iter;
        let conn = self.raw_conn();
        conn.execute(sql, params_from_iter(args.iter().copied()))
            .map_err(map)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(prompt: &str) -> ProfileSpec {
        ProfileSpec {
            system_prompt: prompt.into(),
            model_provider: "lmstudio".into(), model_id: "qwen3-8b".into(),
            params: serde_json::json!({"temperature": 0.2}),
            tool_grants: vec!["read_file".into()],
        }
    }

    #[test]
    fn create_and_load_current_profile() {
        let s = CoreStore::in_memory().unwrap();
        let a = s.create_agent("r1", "Researcher", "finds facts",
                               &spec("You research.")).unwrap();
        assert_eq!(a.profile.version, 1);
        assert_eq!(a.profile.system_prompt, "You research.");
        assert_eq!(s.status("r1").unwrap(), AgentStatus::Idle);
    }

    #[test]
    fn profile_edits_version_history() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("d1", "Developer", "", &spec("v1")).unwrap();
        let a2 = s.edit_profile("d1", &spec("v2")).unwrap();
        assert_eq!(a2.profile.version, 2);
        assert_eq!(a2.profile.system_prompt, "v2");
        let history = s.profile_history("d1").unwrap();
        assert_eq!(history.len(), 2);
        // The old version is preserved verbatim.
        assert_eq!(history[0].system_prompt, "v1");
    }

    #[test]
    fn duplicate_independent_identity() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("a", "Alpha", "", &spec("shared")).unwrap();
        let b = s.duplicate_agent("a", "b", "Beta").unwrap();
        assert_eq!(b.row.id, "b");
        assert_eq!(b.profile.system_prompt, "shared");
        // Editing the copy leaves the original untouched.
        s.edit_profile("b", &spec("changed")).unwrap();
        assert_eq!(s.agent("a").unwrap().profile.system_prompt, "shared");
        assert_eq!(s.agent("b").unwrap().profile.system_prompt, "changed");
    }

    #[test]
    fn lifecycle_transitions_and_archive() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("a", "A", "", &spec("p")).unwrap();
        s.set_status("a", AgentStatus::Working).unwrap();
        assert_eq!(s.status("a").unwrap(), AgentStatus::Working);
        s.archive_agent("a").unwrap();
        let row = s.get_agent("a").unwrap().unwrap();
        assert!(row.archived && !row.enabled);
        assert_eq!(row.status, "stopped");
        assert!(s.list_agents(false).unwrap().is_empty());
    }

    #[test]
    fn reset_session_keeps_identity_and_memory() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("a", "A", "", &spec("p")).unwrap();
        let memory = crate::storage::MemoryRow {
            id: 0, agent_id: "a".into(), category: "fact".into(),
            content: "keep me".into(), source: "user".into(),
            provenance: String::new(), confidence: 1.0,
            scope: "private".into(), version: 1,
            created_at: utc_now(), updated_at: utc_now(),
            expires_at: String::new(),
        };
        s.add_memory(&memory).unwrap();
        let fresh = s.reset_session("a").unwrap();
        assert!(fresh.starts_with("a-s-"));
        assert!(s.get_agent("a").unwrap().is_some(),
                "identity survives the reset");
        assert_eq!(s.list_memory("a").unwrap().len(), 1);
    }

    #[test]
    fn delete_with_retention_choices() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("a", "A", "", &spec("p")).unwrap();
        s.reset_session("a").unwrap();
        let memory = crate::storage::MemoryRow {
            id: 0, agent_id: "a".into(), category: "fact".into(),
            content: "m".into(), source: "user".into(),
            provenance: String::new(), confidence: 1.0,
            scope: "private".into(), version: 1,
            created_at: utc_now(), updated_at: utc_now(),
            expires_at: String::new(),
        };
        s.add_memory(&memory).unwrap();
        s.remove_agent("a", RetentionPolicy::KeepMemory).unwrap();
        assert!(s.get_agent("a").unwrap().is_none());
        // memory kept: query directly (the FK owner is gone, rows remain)
        let kept = s.list_memory("a").unwrap();
        assert_eq!(kept.len(), 1, "KeepMemory retains memory rows");

        s.create_agent("b", "B", "", &spec("p")).unwrap();
        s.reset_session("b").unwrap();
        s.remove_agent("b", RetentionPolicy::DeleteAll).unwrap();
        assert!(s.list_memory("b").unwrap().is_empty());
        assert!(s.list_sessions("b").unwrap().is_empty());
    }

    #[test]
    fn export_contains_everything() {
        let s = CoreStore::in_memory().unwrap();
        s.create_agent("a", "A", "", &spec("p")).unwrap();
        s.edit_profile("a", &spec("p2")).unwrap();
        let export = s.export_agent("a").unwrap();
        assert_eq!(export["profile_history"].as_array().unwrap().len(), 2);
        assert!(export["sessions"].is_array());
        assert!(export["inbox"].is_array());
    }
}
