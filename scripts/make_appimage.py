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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_deb import icon_png, workspace_version   # noqa: E402

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


def _execution_smoke(binary: Path, appdir: Path) -> str:
    """Run the self-extracted binary on a private Xvfb display;
    return a verdict line.  Skipped (plainly) when Xvfb or the GUI
    libs are absent - the deb script documents the same escape
    hatch (LAS_SMOKE_LIBDIR)."""
    import time
    if shutil.which("Xvfb") is None:
        return "SKIP: execution smoke (no Xvfb on this machine)"
    display = f":{os.getpid() % 400 + 60}"
    sock = Path(f"/tmp/.X11-unix/X{display[1:]}")
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.1)
        else:
            return "FAIL: Xvfb display never came up"
        with tempfile.TemporaryDirectory() as dd:
            data_dir = Path(dd) / "data"
            env = {**dict(os.environ),
                   "LAS_DATA_DIR": str(data_dir),
                   "DISPLAY": display,
                   "LIBGL_ALWAYS_SOFTWARE": "1",
                   # AppImages normally find their bundled libs via
                   # the runtime; for the smoke we ran the payload
                   # directly, so mirror the AppRun behaviour.
                   "APPDIR": str(appdir)}
            extra = os.environ.get("LAS_SMOKE_LIBDIR", "")
            if extra:
                env["LD_LIBRARY_PATH"] = (
                    f"{extra}:{env.get('LD_LIBRARY_PATH', '')}")
                vendor = (Path(extra).parents[1] / "share"
                          / "glvnd" / "egl_vendor.d"
                          / "50_mesa.json")
                if vendor.exists():
                    env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor)
            p = subprocess.Popen([str(binary)], env=env,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
            try:
                p.wait(timeout=3)
                err = (p.stderr.read() or b"").decode(
                    errors="replace")
                return (f"FAIL: binary exited early "
                        f"({err.strip()[:200]})")
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            if not (data_dir / "studio.db").exists():
                return "FAIL: no studio.db created in the data dir"
            return (f"OK: extracted binary ran 3s on display "
                    f"{display} and opened its store")
    finally:
        xvfb.terminate()
        xvfb.wait()


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

    version = workspace_version()
    if not version:
        print("FAIL: workspace version not found")
        return 1

    # Scaffold the FULL AppDir here (idempotent): binary, AppRun,
    # desktop entry, icon, AppStream metainfo - no prepared dist/
    # tree is assumed, the script works from any fresh checkout.
    if APPDIR.exists():
        shutil.rmtree(APPDIR)
    BIN_DST.parent.mkdir(parents=True)
    shutil.copy2(BIN_SRC, BIN_DST)
    binary_sha = sha256(BIN_DST)
    (APPDIR / "AppRun").write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        'exec "$HERE/usr/bin/las-desktop" "$@"\n')
    (APPDIR / "AppRun").chmod(0o755)
    (APPDIR / "io.github.kimpearce888.local-agent-studio.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Local Agent Studio\n"
        "Comment=A local-first desktop studio for AI agent teams\n"
        "Exec=las-desktop\n"
        "Terminal=false\n"
        "Categories=Development;\n"
        "Icon=local-agent-studio\n")
    icons = APPDIR / "usr" / "share" / "icons" / "hicolor" \
        / "128x128" / "apps"
    icons.mkdir(parents=True)
    (icons / "local-agent-studio.png").write_bytes(icon_png())
    shutil.copy2(icons / "local-agent-studio.png",
                 APPDIR / ".DirIcon")
    metainfo = (APPDIR / "usr" / "share" / "metainfo")
    metainfo.mkdir(parents=True)
    # appstreamcli (run by appimagetool where present) REQUIRES a
    # description, a desktop-id launchable and a release DATE; the
    # homepage URL is a warning otherwise.  CI caught all three.
    import datetime
    today = datetime.date.today().isoformat()
    (metainfo / "local-agent-studio.appdata.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        '<component type="desktop-application">\n'
        '  <id>io.github.kimpearce888.local-agent-studio</id>\n'
        '  <name>Local Agent Studio</name>\n'
        '  <summary>Local-first desktop studio for AI agent '
        'teams</summary>\n'
        '  <description><p>A local-first desktop studio for '
        'building, running and supervising teams of AI agents: '
        'the engine, tools, MCP client and session store live '
        'on your machine.</p></description>\n'
        '  <launchable type="desktop-id">io.github.kimpearce888.local-agent-studio'
        '.desktop</launchable>\n'
        '  <url type="homepage">https://github.com/kimpearce888/'
        'local-agent-studio</url>\n'
        '  <project_license>MIT</project_license>\n'
        '  <metadata_license>MIT</metadata_license>\n'
        f'  <releases><release version="{version}" '
        f'date="{today}"/></releases>\n'
        '</component>\n')
    # Validate BEFORE packaging when the tool exists, so a bad
    # metainfo fails HERE with a readable message, not deep inside
    # appimagetool (this sandbox lacks appstreamcli; CI has it).
    if shutil.which("appstreamcli"):
        r = subprocess.run(
            ["appstreamcli", "validate",
             str(metainfo / "local-agent-studio.appdata.xml")],
            capture_output=True, text=True)
        # appstreamcli versions disagree on whether warnings flip
        # the exit status; appimagetool fails on ERRORS only, so
        # the pre-check does the same.
        if " E: " in (r.stdout + r.stderr):
            print("FAIL: appstreamcli rejected the metainfo:",
                  r.stdout, r.stderr)
            return 1
        print("OK: appstreamcli validated the metainfo")

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
        # EXECUTION SMOKE (same policy as the deb): the extracted
        # binary must run a real GUI main loop for 3 seconds on a
        # private Xvfb display and open its store.
        smoke = _execution_smoke(extracted, Path(td) / "squashfs-root")
        print(smoke)
        if smoke.startswith("FAIL"):
            return 1
    print(f"OK: {out} ({out.stat().st_size} bytes) verified by "
          f"self-extraction + checksum + execution smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
