#!/usr/bin/env python3
"""Build and verify the .deb artifact (release-policy.md).

Rebuilds dist/local-agent-studio_<ver>_amd64.deb from the release
binary, then verifies it STRUCTURALLY: unpacks with dpkg-deb -x and
compares the payload checksums.  Never claims success without the
verification passing.

Usage: python3 scripts/make_deb.py [--skip-build]
"""
import argparse
import hashlib

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST = ROOT / "rust"
DIST = ROOT / "dist"
DEB_TREE = DIST / "deb"
BIN_SRC = RUST / "target" / "release" / "las-desktop"
BIN_DST = DEB_TREE / "usr" / "local" / "bin" / "las-desktop"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse the existing release binary")
    args = ap.parse_args()

    if not args.skip_build:
        print("cargo build --release -p las-desktop ...")
        r = subprocess.run(
            ["cargo", "build", "--release", "-p", "las-desktop"],
            cwd=RUST)
        if r.returncode != 0:
            print("FAIL: release build failed")
            return 1
    if not BIN_SRC.exists():
        print(f"FAIL: release binary missing: {BIN_SRC}")
        return 1

    # Version straight from the workspace manifest (single source).
    version = None
    for line in (RUST / "Cargo.toml").read_text().splitlines():
        if line.startswith("version ="):
            version = line.split("=")[1].strip().strip('"')
            break
    if not version:
        print("FAIL: workspace version not found")
        return 1

    print(f"version: {version}")
    DEB_TREE.mkdir(parents=True, exist_ok=True)
    BIN_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BIN_SRC, BIN_DST)
    binary_sha = sha256(BIN_DST)

    out = DIST / f"local-agent-studio_{version}_amd64.deb"
    print(f"dpkg-deb --build -> {out.name}")
    r = subprocess.run(
        ["dpkg-deb", "--build", str(DEB_TREE), str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL: dpkg-deb failed:", r.stderr)
        return 1

    # STRUCTURAL VERIFICATION: unpack and compare.
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            ["dpkg-deb", "-x", str(out), td],
            capture_output=True, text=True)
        if r.returncode != 0:
            print("FAIL: dpkg-deb -x failed:", r.stderr)
            return 1
        unpacked = Path(td) / "usr" / "local" / "bin" / "las-desktop"
        if not unpacked.exists():
            print("FAIL: binary missing from the unpacked payload")
            return 1
        if sha256(unpacked) != binary_sha:
            print("FAIL: unpacked binary checksum mismatch")
            return 1
        entry = (Path(td) / "usr" / "share" / "applications"
                 / "local-agent-studio.desktop")
        if not entry.exists():
            print("FAIL: desktop entry missing from the payload")
            return 1
    print(f"OK: {out} ({out.stat().st_size} bytes) verified by "
          f"unpack + checksum")
    return 0


if __name__ == "__main__":
    sys.exit(main())
