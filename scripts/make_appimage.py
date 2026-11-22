#!/usr/bin/env python3
"""Build and verify the AppImage artifact (release-policy.md).

Rebuilds dist/local-agent-studio-<ver>-x86_64.AppImage from the
release binary using appimagetool, then verifies it STRUCTURALLY by
self-extraction (--appimage-extract) and payload comparison.

appimagetool is looked up in $PATH, then the known extracted copy.
If it is absent the script says so plainly and exits non-zero - it
never downloads anything and never claims success without
verification.

Usage: python3 scripts/make_appimage.py [--appimagetool PATH]
                                       [--skip-build]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST = ROOT / "rust"
DIST = ROOT / "dist"
APPDIR = DIST / "appdir"
BIN_SRC = RUST / "target" / "release" / "las-desktop"
BIN_DST = APPDIR / "usr" / "bin" / "las-desktop"
# appimagetool itself ships as an AppImage; the EXTRACTED form
# (squashfs-root/AppRun) runs without FUSE.  Prefer that.
KNOWN_TOOLS = [
    Path("/tmp/my-project/scripts/appimage/squashfs-root/AppRun"),
    Path.home() / ".local/bin/appimagetool",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_appimagetool(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    on_path = shutil.which("appimagetool")
    if on_path:
        return Path(on_path)
    for p in KNOWN_TOOLS:
        if p.exists():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--appimagetool")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    tool = find_appimagetool(args.appimagetool)
    if tool is None:
        print("FAIL: appimagetool not found. Pass --appimagetool "
              "PATH with an extracted copy "
              "(https://github.com/AppImage/appimagetool).")
        return 1
    print(f"appimagetool: {tool}")
    # An unextracted appimagetool AppImage cannot run here (no
    # FUSE); extract it once and use the AppRun inside.
    if tool.suffix == ".AppImage" or tool.name == "appimagetool":
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [str(tool), "--appimage-extract"], cwd=td,
                capture_output=True, text=True)
            inner = Path(td) / "squashfs-root"
            if r.returncode != 0 or not inner.exists():
                print("FAIL: could not extract appimagetool")
                return 1
            keep = Path(tempfile.mkdtemp(prefix="las-ait-"))
            shutil.move(str(inner), keep / "squashfs-root")
        tool = keep / "squashfs-root" / "AppRun"
        print(f"extracted appimagetool: {tool}")

    if not args.skip_build:
        r = subprocess.run(
            ["cargo", "build", "--release", "-p", "las-desktop"],
            cwd=RUST)
        if r.returncode != 0:
            print("FAIL: release build failed")
            return 1
    if not BIN_SRC.exists():
        print(f"FAIL: release binary missing: {BIN_SRC}")
        return 1

    version = None
    for line in (RUST / "Cargo.toml").read_text().splitlines():
        if line.startswith("version ="):
            version = line.split("=")[1].strip().strip('"')
            break
    if not version:
        print("FAIL: workspace version not found")
        return 1

    BIN_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BIN_SRC, BIN_DST)
    binary_sha = sha256(BIN_DST)

    out = DIST / f"local-agent-studio-{version}-x86_64.AppImage"
    env = dict(os.environ, ARCH="x86_64")
    r = subprocess.run(
        [str(tool), str(APPDIR), str(out)],
        env=env, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        print("FAIL: appimagetool failed:", r.stdout, r.stderr)
        return 1

    # STRUCTURAL VERIFICATION: self-extract and compare payloads.
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [str(out), "--appimage-extract"],
            cwd=td, capture_output=True, text=True)
        extracted = Path(td) / "squashfs-root" / "usr" / "bin" \
            / "las-desktop"
        if r.returncode != 0 or not extracted.exists():
            print("FAIL: self-extraction did not yield the binary")
            return 1
        if sha256(extracted) != binary_sha:
            print("FAIL: extracted binary checksum mismatch")
            return 1
    print(f"OK: {out} ({out.stat().st_size} bytes) verified by "
          f"self-extraction + checksum")
    return 0


if __name__ == "__main__":
    sys.exit(main())
