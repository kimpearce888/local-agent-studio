"""Attachments (PLN-7, TLS-8): import files for a goal, give the planner a
manifest with summaries - never whole files - and copy the files into the
run workspace so tasks can read them.

Text extraction is stdlib-only by design (no untrusted parsers): Markdown,
TXT, CSV and JSON are read directly; PDF and DOCX are refused with an
honest message until the desktop importer ships.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json"}
SUMMARY_CHARS = 400
MAX_IMPORT_BYTES = 20 * 1024 * 1024      # 20 MB per file


class AttachmentError(ValueError):
    """An actionable import error (message is user-facing)."""


def extract_text(path: str) -> tuple[bool, str]:
    """(supported, text). Text formats read directly; others refused."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in TEXT_SUFFIXES:
        return False, (f"{suffix or 'no'} files are not importable yet; "
                       f"PDF and DOCX extraction ships with the desktop "
                       f"importer. Save the file as Markdown or TXT.")
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read(MAX_IMPORT_BYTES + 1)
    if len(text) > MAX_IMPORT_BYTES:
        raise AttachmentError(
            f"{os.path.basename(path)} is larger than "
            f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB.")
    if suffix == ".csv":
        # One preview row keeps the manifest small for wide tables.
        rows = list(csv.reader(text.splitlines()))
        text = "\n".join(",".join(r) for r in rows[:5]) + (
            f"\n... ({max(0, len(rows) - 5)} more rows)")
    return True, text


def import_file(path: str, data_dir: str, plan_id: str) -> dict:
    """Import one file into <data>/attachments/<plan_id>/ and return its
    manifest entry (name, size, type, sha256, summary)."""
    if not os.path.isfile(path):
        raise AttachmentError(f"No file {path}.")
    supported, text = extract_text(path)
    if not supported:
        raise AttachmentError(text)
    raw = open(path, "rb").read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise AttachmentError(
            f"{os.path.basename(path)} is larger than "
            f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB.")
    dest_dir = os.path.join(data_dir, "attachments", plan_id)
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(path)
    dest = os.path.join(dest_dir, name)
    shutil.copyfile(path, dest)
    lines = text.count("\n") + 1 if text else 0
    summary = (text[:SUMMARY_CHARS] + ("..." if len(text) > SUMMARY_CHARS
                                        else "")) if supported else ""
    return {
        "name": name, "path": dest,
        "size_bytes": len(raw),
        "type": "text/" + os.path.splitext(name)[1].lstrip(".").lower(),
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
        "supported": supported,
        "summary": summary, "lines": lines,
        "reason": "" if supported else text,
    }


def manifest_block(entries: list[dict]) -> str:
    """The planner-facing manifest (PLN-7): names, sizes and summaries,
    never whole files."""
    if not entries:
        return ""
    lines = ["ATTACHMENTS (manifest with summaries; the files wait in the "
             "run workspace):"]
    for e in entries:
        lines.append(f"- {e['name']} ({e['size_bytes']} bytes, {e['lines']} lines, "
                     f"sha256 {e['sha256']})")
        if e["summary"]:
            first = e["summary"].replace("\n", " ")[:160]
            lines.append(f"  begins: {first}")
    return "\n".join(lines)


def copy_to_workspace(data_dir: str, plan_id: str, workspace_dir: str) -> list[str]:
    """Copy a plan's attachments into the run workspace (TLS-8)."""
    src_dir = os.path.join(data_dir, "attachments", plan_id)
    if not os.path.isdir(src_dir):
        return []
    os.makedirs(workspace_dir, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(src_dir)):
        shutil.copyfile(os.path.join(src_dir, name),
                        os.path.join(workspace_dir, name))
        copied.append(name)
    return copied


__all__ = ["AttachmentError", "copy_to_workspace", "extract_text",
           "import_file", "manifest_block"]
