"""Event record and event types (spec 6.3) — shared kernel contract.

The ``events`` table (owned by the storage module) is the source of truth
for runs; execution writes events through the storage port and rebuilds
run state by replay, so the Event shape is shared by both sides.
"""

from __future__ import annotations

from dataclasses import dataclass

EVENT_TYPES = (
    "run.created", "run.paused", "run.resumed", "run.cancelled",
    "task.ready", "task.stale",
    "gate.waiting", "gate.approved", "gate.rejected",
    "attempt.started", "attempt.aborted",
    "message.added", "tool.called", "tool.denied",
    "compaction.applied",
    "handoff.recorded", "handoff.edited",
    "constraint.released",
    "task.succeeded", "task.failed", "task.skipped",
    "run.succeeded", "run.failed",
)


@dataclass
class Event:
    event_id: str
    run_id: str
    seq: int
    ts: str
    schema_version: int
    type: str
    payload: dict
    prev_hash: str
    hash: str

    def to_obj(self) -> dict:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": self.ts,
            "schema_version": self.schema_version,
            "type": self.type,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }
