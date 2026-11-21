"""Durable storage: the hash-chained event log and user data (spec 6.3-6.5).

The ``events`` table is the source of truth for runs.  Every event carries
``prev_hash`` and ``hash``; a run's chain is verified whenever the run is
opened, and a tampered run is refused (spec 12).  User data (providers,
plans, templates, role profiles, settings) lives in plain tables.  Derived
state (tasks, attempts, handoffs) is rebuilt by replaying events
(las.modules.execution); persistence of derived tables is an M1 optimization.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from las import APP_NAME, SCHEMA_VERSION
from las.shared.events_contract import EVENT_TYPES, Event
from las.shared.provider_contract import ProviderProfile

DB_SCHEMA_VERSION = 1

SETTINGS_DEFAULTS: dict[str, Any] = {
    "ui.theme": "system",
    "ui.reduce_motion": "system",
    "ui.text_scale": 100,
    "runs.failure_policy": "ask",          # ask | stop | skip
    "runs.cap_tokens": 2_000_000,
    "runs.cap_wall_minutes": 480,
    "runs.max_output_tokens": 4096,
    "data.retention": "keep forever",
    "export.ai_notice": True,
    "export.redact_prompts": False,
    "updates.channel": "stable",
    "updates.auto": False,
    "privacy.crash_reports": False,
}


class DBNewerError(Exception):
    """The database was written by a newer app version (message DB-NEWER)."""


class TamperedLogError(Exception):
    """The event hash chain does not verify for a run."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    """SQLite store + run directory layout for one data root."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "plans"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "templates"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "runs"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "backups"), exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "studio.db")
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    # -- schema ----------------------------------------------------------

    def _ensure_schema(self) -> None:
        db = self.db
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ts TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                UNIQUE(run_id, seq));
            CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY, obj TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, obj TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS templates (
                id TEXT PRIMARY KEY, obj TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS role_profiles (
                id TEXT PRIMARY KEY, obj TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        row = db.execute("SELECT value FROM meta WHERE key='db_schema_version'").fetchone()
        if row is None:
            db.execute("INSERT INTO meta (key, value) VALUES ('db_schema_version', ?)",
                       (str(DB_SCHEMA_VERSION),))
            db.execute("INSERT INTO meta (key, value) VALUES ('app_version', ?)", ("0.1.0",))
        else:
            stored = int(row["value"])
            if stored > DB_SCHEMA_VERSION:
                raise DBNewerError(
                    f"database schema {stored} is newer than supported {DB_SCHEMA_VERSION}")
            if stored < DB_SCHEMA_VERSION:
                self._backup(f"migrate_v{stored}_to_v{DB_SCHEMA_VERSION}")
                db.execute("UPDATE meta SET value=? WHERE key='db_schema_version'",
                           (str(DB_SCHEMA_VERSION),))
        db.commit()

    def _backup(self, tag: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = os.path.join(self.data_dir, "backups", f"studio.db.{ts}.{tag}.bak")
        shutil.copy2(self.db_path, dest)

    def close(self) -> None:
        self.db.close()

    # -- events (spec 6.3) -------------------------------------------------

    def append(self, run_id: str, type_: str, payload: dict | None = None) -> Event:
        """Append one event, extending the hash chain. Returns the event."""
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        payload = payload or {}
        row = self.db.execute(
            "SELECT seq, hash FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,)).fetchone()
        seq = (row["seq"] + 1) if row else 1
        prev_hash = row["hash"] if row else "0" * 64
        ts = utc_now_iso()
        event_id = secrets.token_hex(8)
        schema_version = SCHEMA_VERSION
        h = hashlib.sha256(
            (prev_hash + "\n" + canonical_json(
                [run_id, seq, ts, schema_version, type_, payload])).encode("utf-8")).hexdigest()
        event = Event(event_id, run_id, seq, ts, schema_version, type_, payload, prev_hash, h)
        self.db.execute(
            "INSERT INTO events (event_id, run_id, seq, ts, schema_version, type, payload,"
            " prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (event.event_id, run_id, seq, ts, schema_version, type_,
             canonical_json(payload), prev_hash, h))
        self.db.commit()
        return event

    def events(self, run_id: str, after: int = 0) -> list[Event]:
        rows = self.db.execute(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after))
        return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, r: sqlite3.Row) -> Event:
        return Event(r["event_id"], r["run_id"], r["seq"], r["ts"], r["schema_version"],
                     r["type"], json.loads(r["payload"]), r["prev_hash"], r["hash"])

    def verify(self, run_id: str) -> bool:
        """Verify a run's hash chain (spec 12)."""
        prev = "0" * 64
        for e in self.events(run_id):
            if e.seq == 1:
                prev = "0" * 64
            if e.prev_hash != prev:
                return False
            h = hashlib.sha256(
                (e.prev_hash + "\n" + canonical_json(
                    [e.run_id, e.seq, e.ts, e.schema_version, e.type, e.payload])
                ).encode("utf-8")).hexdigest()
            if h != e.hash:
                return False
            prev = e.hash
        return True

    def run_ids(self) -> list[str]:
        rows = self.db.execute(
            "SELECT run_id, MIN(rowid) AS first FROM events GROUP BY run_id"
            " ORDER BY first DESC")
        return [r["run_id"] for r in rows]

    # -- run directory layout (spec 6.4) ------------------------------------

    def run_dir(self, run_id: str) -> str:
        return os.path.join(self.data_dir, "runs", run_id)

    def workspace_dir(self, run_id: str) -> str:
        return os.path.join(self.run_dir(run_id), "workspace")

    def attempt_dir(self, run_id: str, task_id: str, attempt_id: str) -> str:
        return os.path.join(self.run_dir(run_id), "attempts", task_id, attempt_id)

    def handoff_path(self, run_id: str, task_id: str) -> str:
        return os.path.join(self.run_dir(run_id), "handoffs", f"{task_id}.json")

    def export_dir(self, run_id: str) -> str:
        return os.path.join(self.run_dir(run_id), "export")

    def write_attempt_file(self, run_id: str, task_id: str, attempt_id: str,
                           name: str, content: str, append: bool = False) -> str:
        d = self.attempt_dir(run_id, task_id, attempt_id)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        mode = "a" if append else "w"
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(content)
        return path

    def write_handoff(self, run_id: str, task_id: str, handoff_obj: dict) -> str:
        path = self.handoff_path(run_id, task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(handoff_obj, fh, ensure_ascii=False, indent=2)
        return path

    def read_handoff(self, run_id: str, task_id: str) -> Optional[dict]:
        path = self.handoff_path(run_id, task_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- providers ----------------------------------------------------------

    def save_provider(self, profile: ProviderProfile) -> None:
        self.db.execute(
            "INSERT INTO providers (id, obj) VALUES (?,?)"
            " ON CONFLICT(id) DO UPDATE SET obj=excluded.obj",
            (profile.id, canonical_json(profile.to_obj())))
        self.db.commit()

    def get_provider(self, provider_id: str) -> Optional[ProviderProfile]:
        row = self.db.execute("SELECT obj FROM providers WHERE id=?", (provider_id,)).fetchone()
        return ProviderProfile.from_obj(json.loads(row["obj"])) if row else None

    def list_providers(self) -> list[ProviderProfile]:
        rows = self.db.execute("SELECT obj FROM providers ORDER BY id")
        return [ProviderProfile.from_obj(json.loads(r["obj"])) for r in rows]

    def delete_provider(self, provider_id: str) -> None:
        self.db.execute("DELETE FROM providers WHERE id=?", (provider_id,))
        self.db.commit()

    # -- plans / templates / role profiles ------------------------------------

    def save_plan(self, plan_obj: dict) -> None:
        self.db.execute(
            "INSERT INTO plans (id, obj, created_at) VALUES (?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET obj=excluded.obj",
            (plan_obj["id"], canonical_json(plan_obj), plan_obj.get("created_at", "")))
        self.db.commit()
        path = os.path.join(self.data_dir, "plans", f"{plan_obj['id']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan_obj, fh, ensure_ascii=False, indent=2)

    def get_plan(self, plan_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT obj FROM plans WHERE id=?", (plan_id,)).fetchone()
        return json.loads(row["obj"]) if row else None

    def save_template(self, template: dict) -> None:
        self.db.execute(
            "INSERT INTO templates (id, obj) VALUES (?,?)"
            " ON CONFLICT(id) DO UPDATE SET obj=excluded.obj",
            (template["id"], canonical_json(template)))
        self.db.commit()

    def list_templates(self) -> list[dict]:
        rows = self.db.execute("SELECT obj FROM templates ORDER BY id")
        return [json.loads(r["obj"]) for r in rows]

    # -- settings (spec 6.5) ---------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return json.loads(row["value"])
        if key in SETTINGS_DEFAULTS:
            return SETTINGS_DEFAULTS[key]
        return default

    def set_setting(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, canonical_json(value)))
        self.db.commit()



__all__ = [
    "DB_SCHEMA_VERSION",
    "SETTINGS_DEFAULTS",
    "EVENT_TYPES",
    "DBNewerError",
    "TamperedLogError",
    "utc_now_iso",
    "canonical_json",
    "Store",
]
