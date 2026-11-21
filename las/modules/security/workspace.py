"""The per-run workspace: a jailed file area for agent tools (TLS-1).

Every operation resolves the target path with :func:`os.path.realpath` and
verifies it stays inside the run folder, so symlinks cannot escape.  Size
limits (5 MB per file, 200 MB per run, spec 5.8) are enforced on write.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

MAX_FILE_BYTES = 5 * 1024 * 1024        # 5 MB per file
MAX_TOTAL_BYTES = 200 * 1024 * 1024     # 200 MB per run
MANIFEST_MAX_ENTRIES = 200              # spec 5.4 block 6
MANIFEST_COMPACT_ENTRIES = 50           # compaction ladder step 3


class WorkspaceError(Exception):
    """A tool-level failure with a rule name the broker can audit."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


class Workspace:
    def __init__(self, root: str, max_file_bytes: int = MAX_FILE_BYTES,
                 max_total_bytes: int = MAX_TOTAL_BYTES):
        self.root = os.path.realpath(os.path.abspath(root))
        os.makedirs(self.root, exist_ok=True)
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    # -- jail ---------------------------------------------------------------

    def resolve(self, rel_path: str) -> str:
        """Resolve a workspace-relative path inside the jail.

        Raises WorkspaceError('path-escape') when the path is absolute,
        climbs out of the workspace, or resolves through a symlink outside
        the jail.
        """
        if not isinstance(rel_path, str) or not rel_path.strip():
            raise WorkspaceError("path-escape", "The path is empty.")
        if rel_path.startswith("/") or rel_path.startswith("\\") or re.match(r"^[A-Za-z]:", rel_path):
            raise WorkspaceError("path-escape", f"The path {rel_path!r} is absolute.")
        candidate = os.path.join(self.root, rel_path)
        target = os.path.realpath(candidate)
        if target != self.root and not target.startswith(self.root + os.sep):
            raise WorkspaceError(
                "path-escape", f"The path {rel_path!r} resolves outside the workspace.")
        if os.path.sep != "/":
            # Also guard the textual form on this platform.
            norm = os.path.normpath(rel_path)
            if norm.startswith(".."):
                raise WorkspaceError("path-escape", f"The path {rel_path!r} climbs out of the workspace.")
        return target

    # -- queries --------------------------------------------------------------

    def exists(self, rel_path: str) -> bool:
        return os.path.exists(self.resolve(rel_path))

    def is_file(self, rel_path: str) -> bool:
        return os.path.isfile(self.resolve(rel_path))

    def sha256(self, rel_path: str) -> str:
        path = self.resolve(rel_path)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def size(self, rel_path: str) -> int:
        return os.path.getsize(self.resolve(rel_path))

    def total_bytes(self) -> int:
        total = 0
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        return total

    # -- file tools ---------------------------------------------------------------

    def list_files(self, prefix: str = "") -> list[str]:
        base = self.resolve(prefix) if prefix else self.root
        if not os.path.isdir(base):
            raise WorkspaceError("not-found", f"{prefix!r} is not a folder.")
        out: list[str] = []
        for dirpath, dirnames, files in os.walk(base):
            dirnames.sort()
            for name in sorted(files):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                out.append(rel)
            if len(out) > 2000:
                break
        return out

    def read_file(self, rel_path: str, max_bytes: Optional[int] = None) -> str:
        path = self.resolve(rel_path)
        if not os.path.isfile(path):
            raise WorkspaceError("not-found", f"The file {rel_path!r} does not exist.")
        limit = max_bytes or self.max_file_bytes
        if os.path.getsize(path) > limit:
            raise WorkspaceError(
                "size-limit", f"The file {rel_path!r} is larger than {limit} bytes.")
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def write_file(self, rel_path: str, content: str, append: bool = False) -> str:
        path = self.resolve(rel_path)
        if os.path.isdir(path):
            raise WorkspaceError("is-dir", f"{rel_path!r} is a folder.")
        data = content.encode("utf-8")
        existing = os.path.getsize(path) if os.path.exists(path) else 0
        new_size = (existing + len(data)) if append else len(data)
        if new_size > self.max_file_bytes:
            raise WorkspaceError(
                "size-limit",
                f"Writing {rel_path!r} would exceed the {self.max_file_bytes} byte file limit.")
        if self.total_bytes() - existing + new_size > self.max_total_bytes:
            raise WorkspaceError(
                "size-limit", "The run workspace has reached its total size limit.")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "a" if append else "w"
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(content)
        return f"wrote {new_size} bytes to {rel_path}"

    def search_files(self, pattern: str, path_prefix: str = "") -> list[dict]:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError("bad-args", f"The pattern does not compile: {exc}") from exc
        matches: list[dict] = []
        for rel in self.list_files(path_prefix):
            try:
                text = self.read_file(rel, max_bytes=1024 * 1024)
            except WorkspaceError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    matches.append({"path": rel, "line": lineno,
                                    "text": line[:200]})
                    if len(matches) >= 200:
                        return matches
        return matches

    def patch_file(self, rel_path: str, find: str, replace: str, all_occurrences: bool = True) -> str:
        text = self.read_file(rel_path)
        count = text.count(find)
        if count == 0:
            return f"no occurrences of the pattern in {rel_path}"
        if all_occurrences:
            new_text = text.replace(find, replace)
        else:
            new_text = text.replace(find, replace, 1)
        self.write_file(rel_path, new_text)
        return f"replaced {count if all_occurrences else min(count, 1)} occurrence(s) in {rel_path}"

    def delete_file(self, rel_path: str) -> str:
        path = self.resolve(rel_path)
        if os.path.isfile(path):
            os.remove(path)
            return f"deleted {rel_path}"
        raise WorkspaceError("not-found", f"The file {rel_path!r} does not exist.")

    # -- manifest (spec 5.4 block 6) -------------------------------------------

    def manifest(self, max_entries: int = MANIFEST_MAX_ENTRIES) -> list[dict]:
        """Paths, sizes and hashes, up to ``max_entries``."""
        entries: list[dict] = []
        for rel in self.list_files()[:max_entries]:
            try:
                entries.append({
                    "path": rel,
                    "size": self.size(rel),
                    "sha256": self.sha256(rel),
                })
            except (WorkspaceError, OSError):
                continue
        return entries
