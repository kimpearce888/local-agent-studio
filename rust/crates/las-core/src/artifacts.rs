//! Artifacts as versioned first-class entities (mandate 10).
//!
//! An artifact is an EXPLICIT reference to a file inside an owner
//! agent's workspace jail: it carries versions (every registration
//! of the same logical id appends the next version with a fresh
//! sha256), ownership (the owner agent), provenance (JSON: who
//! registered it and through what - a tool call, a run, the user),
//! an optional project sharing scope, a capped preview, an export
//! primitive, and deletion.
//!
//! Sharing never punches through the jail: `export_artifact` COPIES
//! the bytes into the RECIPIENT's workspace as a new file, recorded
//! in provenance - the recipient reads its own copy through its own
//! jailed tools.  Agents never gain access to another agent's
//! workspace by being in a project; they see the artifact LIST and
//! must export explicitly (mandate 10: "Use explicit artifact
//! references to share files").

use crate::storage::{ArtifactRow, CoreStore};
use sha2::{Digest, Sha256};

/// Previews are cut here so a UI label can carry them.
pub const PREVIEW_CAP_BYTES: usize = 8 * 1024;

/// The largest file that may be exported (matches the tool engine's
/// 5 MB write cap).
pub const EXPORT_CAP_BYTES: u64 = crate::tools::MAX_WRITE_BYTES as u64;


/// Lowercase hex without a dependency (the workspace keeps its
/// dependency list deliberate).
fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

fn workspace_of(store: &CoreStore, agent_id: &str)
                -> Result<std::path::PathBuf, String> {
    let Some(root) = store.data_dir() else {
        return Err("in-memory store has no workspace files".into());
    };
    let dir = root.join("workspaces").join(agent_id);
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("workspace: {e}"))?;
    Ok(dir)
}

/// The jailed path of `rel` under the owner's workspace, refusing
/// traversal outside it (same discipline as the tool engine).
fn jailed(dir: &std::path::Path, rel: &str)
          -> Result<std::path::PathBuf, String> {
    let p = std::path::Path::new(rel);
    if p.is_absolute() {
        return Err("artifact paths are workspace-relative".into());
    }
    let joined = dir.join(p);
    // Component-level refusal of `..` (the canonicalized-parent
    // check happens on the real filesystem below).
    for c in p.components() {
        if matches!(c,
                    std::path::Component::ParentDir) {
            return Err("path escapes the workspace jail".into());
        }
    }
    let parent = joined.parent().unwrap_or(&joined);
    let canon_parent = parent.canonicalize()
        .map_err(|_| "artifact directory does not exist".to_string())?;
    let canon_root = dir.canonicalize()
        .map_err(|_| "workspace jail missing".to_string())?;
    if !canon_parent.starts_with(&canon_root) {
        return Err("path escapes the workspace jail".into());
    }
    Ok(joined)
}

/// Register a workspace file as an artifact, or - when
/// `artifact_id` names an existing artifact - append its next
/// VERSION (fresh hash, fresh provenance).  Returns the stored row.
pub fn register(
    store: &CoreStore,
    owner: &str,
    rel_path: &str,
    name: &str,
    project_id: &str,
    provenance: &serde_json::Value,
    artifact_id: Option<&str>,
) -> Result<ArtifactRow, String> {
    let dir = workspace_of(store, owner)?;
    let path = jailed(&dir, rel_path)?;
    let bytes = std::fs::read(&path)
        .map_err(|e| format!("read {rel_path}: {e}"))?;
    let sha = Sha256::digest(&bytes);
    let id = match artifact_id {
        Some(existing) => {
            // A version bump must not silently hijack another
            // owner's artifact.
            if let Ok(Some(row)) = store.latest_artifact(existing) {
                if row.agent_id != owner {
                    return Err(format!(
                        "artifact {existing} is owned by {}",
                        row.agent_id));
                }
            }
            existing.to_string()
        }
        None => format!("art-{}", hex(&sha[..6])),
    };
    let row = ArtifactRow {
        id: 0,
        artifact_id: id,
        version: 0,   // allocated by SQL
        name: name.to_string(),
        agent_id: owner.to_string(),
        project_id: project_id.to_string(),
        rel_path: rel_path.to_string(),
        sha256: hex(&sha),
        size: bytes.len() as i64,
        provenance: provenance.to_string(),
        created_at: crate::storage::utc_now(),
    };
    store.insert_artifact(&row)
        .map_err(|e| e.to_string())?;
    store.latest_artifact(&row.artifact_id)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "insert lost".to_string())
}

/// What one owner can see: its own artifacts plus artifacts shared
/// with the project (latest version of each).
pub fn list(store: &CoreStore, owner: &str, project_id: &str)
            -> Vec<ArtifactRow> {
    store.list_artifacts(owner, project_id).unwrap_or_default()
}

/// Full version history of one artifact, oldest first.
pub fn history(store: &CoreStore, artifact_id: &str)
               -> Vec<ArtifactRow> {
    store.artifact_history(artifact_id).unwrap_or_default()
}

/// A capped text preview of the artifact's CURRENT bytes.
pub fn preview(store: &CoreStore, artifact_id: &str)
               -> Result<String, String> {
    let row = store.latest_artifact(artifact_id)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("unknown artifact {artifact_id}"))?;
    let dir = workspace_of(store, &row.agent_id)?;
    let path = jailed(&dir, &row.rel_path)?;
    let bytes = std::fs::read(&path)
        .map_err(|e| format!("read {}: {e}", row.rel_path))?;
    if bytes.len() as u64 > crate::tools::MAX_WRITE_BYTES as u64 {
        return Err("file exceeds the 5 MB preview cap".into());
    }
    let mut text = String::from_utf8_lossy(&bytes).into_owned();
    if text.len() > PREVIEW_CAP_BYTES {
        text.truncate(PREVIEW_CAP_BYTES);
        text.push_str("\n(preview cut at 8 KB; the file is larger)");
    }
    Ok(text)
}

/// Copy an artifact's CURRENT bytes into another agent's workspace
/// (`to_dir_prefix` keeps the name collision-free) and return the
/// new workspace-relative path.  This is the sharing primitive: an
/// explicit, recorded copy - never a jail bypass.
pub fn export_artifact(
    store: &CoreStore,
    artifact_id: &str,
    to_agent: &str,
) -> Result<String, String> {
    let row = store.latest_artifact(artifact_id)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("unknown artifact {artifact_id}"))?;
    if (row.size as u64) > EXPORT_CAP_BYTES {
        return Err("artifact exceeds the 5 MB export cap".into());
    }
    let src_dir = workspace_of(store, &row.agent_id)?;
    let src = jailed(&src_dir, &row.rel_path)?;
    let bytes = std::fs::read(&src)
        .map_err(|e| format!("read {}: {e}", row.rel_path))?;
    // Integrity: the registry's hash must match the bytes copied.
    let sha = hex(&Sha256::digest(&bytes));
    if sha != row.sha256 {
        return Err(format!(
            "artifact {artifact_id} v{} changed on disk since \\
             registration (hash mismatch)", row.version));
    }
    let dst_dir = workspace_of(store, to_agent)?;
    let rel = format!("shared/{}-v{}-{}",
                      row.artifact_id, row.version,
                      row.name.replace('/', "_"));
    // The export lands under shared/: create the subdirectory
    // BEFORE the jail check canonicalizes it.
    std::fs::create_dir_all(dst_dir.join("shared"))
        .map_err(|e| format!("export dir: {e}"))?;
    let dst = jailed(&dst_dir, &rel)?;
    std::fs::write(&dst, &bytes)
        .map_err(|e| format!("write export: {e}"))?;
    Ok(rel)
}

/// Delete an artifact (every version).  The registry entry goes;
/// the owner's workspace file is the agent's own and stays - the
/// deletion removes the REFERENCE, which is what sharing depends
/// on.  Returns the number of versions removed.
pub fn delete(store: &CoreStore, artifact_id: &str)
              -> Result<usize, String> {
    store.delete_artifact(artifact_id)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn file_store(tag: &str) -> Arc<CoreStore> {
        let dir = std::env::temp_dir().join(format!(
            "las-artifacts-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Arc::new(CoreStore::open(dir.join("t.db")).unwrap())
    }

    fn write_file(store: &CoreStore, agent: &str, rel: &str,
                  content: &str) {
        let dir = workspace_of(&store, agent).unwrap();
        let p = jailed(&dir, rel).unwrap();
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(p, content).unwrap();
    }

    #[test]
    fn register_versions_and_history() {
        let store = file_store("reg");
        write_file(&store, "a", "report.md", "version one");
        let row = register(&store, "a", "report.md", "report.md",
                           "", &serde_json::json!(
                               {"source": "user"}),
                           None).unwrap();
        assert_eq!(row.version, 1);
        assert_eq!(row.agent_id, "a");
        assert_eq!(row.size, "version one".len() as i64);
        assert!(row.sha256.len() == 64);

        // Same file, new content, SAME logical id -> version 2.
        write_file(&store, "a", "report.md", "version two!");
        let row2 = register(&store, "a", "report.md", "report.md",
                            "", &serde_json::json!(
                                {"source": "tool:write_file"}),
                            Some(&row.artifact_id)).unwrap();
        assert_eq!(row2.version, 2);
        assert_ne!(row.sha256, row2.sha256);

        let h = history(&store, &row.artifact_id);
        assert_eq!(h.len(), 2, "the full version history is kept");
        assert_eq!(h[0].sha256, row.sha256);

        // The hash is really sha256 of the bytes.
        let mut expect = Sha256::new();
        expect.update(b"version two!");
        assert_eq!(row2.sha256,
                   hex(&expect.finalize()));
    }

    #[test]
    fn ownership_cannot_be_hijacked() {
        let store = file_store("own");
        write_file(&store, "a", "f.txt", "a's file");
        let row = register(&store, "a", "f.txt", "f.txt", "",
                           &serde_json::json!({}), None).unwrap();
        write_file(&store, "b", "f.txt", "b's file");
        let err = register(&store, "b", "f.txt", "f.txt", "",
                           &serde_json::json!({}),
                           Some(&row.artifact_id))
            .expect_err("another agent cannot bump a's artifact");
        assert!(err.contains("owned by a"), "{err}");
    }

    #[test]
    fn jail_rules_apply_to_registration() {
        let store = file_store("jail");
        let err = register(&store, "a", "../escape.txt", "e", "",
                           &serde_json::json!({}), None)
            .expect_err("traversal refused");
        assert!(err.contains("escapes"), "{err}");
        let err = register(&store, "a", "/abs/path.txt", "e", "",
                           &serde_json::json!({}), None)
            .expect_err("absolute paths refused");
        assert!(err.contains("workspace-relative"), "{err}");
    }

    #[test]
    fn project_sharing_lists_but_never_bypasses_the_jail() {
        let store = file_store("share");
        write_file(&store, "a", "spec.md", "the spec");
        let row = register(&store, "a", "spec.md", "spec.md",
                           "proj-1", &serde_json::json!(
                               {"source": "user"}), None).unwrap();
        // Agent b, same project, SEES the shared artifact...
        let seen = list(&store, "b", "proj-1");
        assert_eq!(seen.len(), 1);
        assert_eq!(seen[0].artifact_id, row.artifact_id);
        assert_eq!(seen[0].agent_id, "a");
        // ...agent c in NO project does not.
        assert!(list(&store, "c", "").is_empty());
        // The owner always sees its own.
        assert_eq!(list(&store, "a", "").len(), 1);

        // Export copies the bytes INTO b's workspace; b reads its
        // own copy, never a's jail.
        let rel = export_artifact(&store, &row.artifact_id, "b")
            .unwrap();
        assert!(rel.starts_with("shared/"), "{rel}");
        let b_dir = workspace_of(&store, "b").unwrap();
        let copied = std::fs::read(b_dir.join(&rel)).unwrap();
        assert_eq!(copied, b"the spec");
        // The copy verifies against the registered hash.
        assert_eq!(hex(&Sha256::digest(&copied)),
                   row.sha256);
    }

    #[test]
    fn export_detects_tampering_after_registration() {
        let store = file_store("tamper");
        write_file(&store, "a", "data.txt", "original");
        let row = register(&store, "a", "data.txt", "data.txt",
                           "", &serde_json::json!({}), None)
            .unwrap();
        // The file changes on disk AFTER registration.
        write_file(&store, "a", "data.txt", "tampered!!");
        let err = export_artifact(&store, &row.artifact_id, "b")
            .expect_err("hash mismatch refused");
        assert!(err.contains("changed on disk"), "{err}");
    }

    #[test]
    fn preview_is_capped_and_current() {
        let store = file_store("prev");
        write_file(&store, "a", "notes.txt", "hello notes");
        let row = register(&store, "a", "notes.txt", "notes.txt",
                           "", &serde_json::json!({}), None)
            .unwrap();
        assert_eq!(preview(&store, &row.artifact_id).unwrap(),
                   "hello notes");
        write_file(&store, "a", "notes.txt", "updated notes");
        assert_eq!(preview(&store, &row.artifact_id).unwrap(),
                   "updated notes", "preview reads current bytes");
        let err = preview(&store, "art-nope")
            .expect_err("unknown artifact");
        assert!(err.contains("unknown"), "{err}");
    }

    #[test]
    fn delete_removes_the_reference_not_the_file() {
        let store = file_store("del");
        write_file(&store, "a", "keep.txt", "content");
        let row = register(&store, "a", "keep.txt", "keep.txt",
                           "", &serde_json::json!({}), None)
            .unwrap();
        write_file(&store, "a", "keep.txt", "v2 content");
        register(&store, "a", "keep.txt", "keep.txt", "",
                 &serde_json::json!({}),
                 Some(&row.artifact_id)).unwrap();
        assert_eq!(delete(&store, &row.artifact_id).unwrap(), 2,
                   "every version goes");
        assert!(list(&store, "a", "").is_empty());
        assert!(preview(&store, &row.artifact_id).is_err());
        // The agent's own file is untouched.
        let dir = workspace_of(&store, "a").unwrap();
        assert_eq!(std::fs::read_to_string(
            dir.join("keep.txt")).unwrap(), "v2 content");
    }
}
