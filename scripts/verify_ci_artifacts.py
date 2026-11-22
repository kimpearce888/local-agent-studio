#!/usr/bin/env python3
"""Download the CI-built desktop artifacts and re-verify them on
a DIFFERENT machine (closing the loop: what GitHub Actions
uploaded actually works outside the runner).  This caught three
real bugs in a row: a glibc-2.43 binary from ubuntu-latest, a
libc6-i386 Depends leak, and floors that never fired.  Verification per artifact:

.deb   - dpkg-deb -I control fields (package, version, arch,
         the GUI runtime Depends set)
       - dpkg-deb -x: binary, RDNS desktop entry, icon, copyright
       - EXECUTION: the unpacked binary runs a real GUI main loop
         for 3s on a private Xvfb display (GUI libs via
         LAS_SMOKE_LIBDIR when the host lacks them) and opens its
         SQLite store
AppImage - runtime self-extraction (--appimage-extract needs no
         FUSE), payload present (AppRun, desktop entry, metainfo,
         binary)
       - EXECUTION smoke on the extracted binary, same rules

Usage: python3 verify_ci_artifacts.py <run-id> [--keep]
Artifacts land in /tmp/ciart/ (kept with --keep).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_API = "https://api.github.com/repos/kimpearce888/local-agent-studio"
RDNS = "io.github.kimpearce888.local-agent-studio"
SMOKE_LIBDIR = os.environ.get("LAS_SMOKE_LIBDIR", "")


def token() -> str:
    r = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    print("FAIL: no GitHub token available")
    sys.exit(1)


def api(url: str, tok: str, dest: Path | None = None) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if dest is not None:
        dest.write_bytes(data)
    return data


def api_download(url: str, tok: str, dest: Path) -> None:
    """Artifact zips 302 to a SIGNED objects.githubusercontent.com
    URL that rejects the Authorization header - fetch the redirect
    target with NO credentials."""
    r = subprocess.run(
        ["curl", "-sS", "--max-time", "120", "-D", "-", "-o",
         "/dev/null", "-H", f"Authorization: Bearer {tok}", url],
        capture_output=True, text=True)
    location = None
    for line in r.stdout.splitlines():
        if line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
    if not location:
        print("FAIL: no redirect for the artifact download",
              r.stdout[:200], r.stderr[:200])
        sys.exit(1)
    subprocess.run(["curl", "-sS", "--max-time", "300", "-o",
                    str(dest), location], check=True)


def download_artifacts(run_id: str, tok: str, outdir: Path) -> None:
    arts = json.loads(api(
        f"{REPO_API}/actions/runs/{run_id}/artifacts", tok))
    found = False
    for a in arts.get("artifacts", []):
        if a["name"] != "local-agent-studio-desktop-linux":
            continue
        found = True
        z = outdir / "artifacts.zip"
        api_download(f"{REPO_API}/actions/artifacts/{a['id']}/zip",
                     tok, z)
        subprocess.run(["unzip", "-o", str(z), "-d", str(outdir)],
                       check=True, capture_output=True)
    if not found:
        print("FAIL: no 'local-agent-studio-desktop-linux' artifact "
              f"on run {run_id}; has the job uploaded yet?")
        sys.exit(1)


def smoke(binary: Path) -> str:
    if shutil.which("Xvfb") is None:
        return "SKIP (no Xvfb)"
    display = f":{os.getpid() % 300 + 100}"
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
            return "FAIL: Xvfb never came up"
        with tempfile.TemporaryDirectory() as dd:
            data_dir = Path(dd) / "data"
            env = {**dict(os.environ),
                   "LAS_DATA_DIR": str(data_dir),
                   "DISPLAY": display,
                   "LIBGL_ALWAYS_SOFTWARE": "1"}
            if Path(SMOKE_LIBDIR).exists():
                env["LD_LIBRARY_PATH"] = (
                    f"{SMOKE_LIBDIR}:"
                    f"{env.get('LD_LIBRARY_PATH', '')}")
                vendor = (Path(SMOKE_LIBDIR).parents[1] / "share"
                          / "glvnd" / "egl_vendor.d" / "50_mesa.json")
                if vendor.exists():
                    env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor)
            p = subprocess.Popen(
                [str(binary)], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                p.wait(timeout=3)
                err = (p.stderr.read() or b"").decode(errors="replace")
                return f"FAIL: exited early ({err.strip()[:160]})"
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            if not (data_dir / "studio.db").exists():
                return "FAIL: no studio.db created"
            return "OK: ran 3s, opened its store"
    finally:
        xvfb.terminate()
        xvfb.wait()


def verify_deb(deb: Path) -> bool:
    ok = True
    info = subprocess.run(["dpkg-deb", "-I", str(deb)],
                          capture_output=True, text=True).stdout
    for field in ("Package: local-agent-studio",
                  "Version: 0.4.1",
                  "Architecture: amd64",
                  "Depends:"):
        if field not in info:
            print(f"FAIL deb: control field missing: {field}")
            ok = False
    for dep in ("libxkbcommon-x11-0", "libegl1", "libvulkan1"):
        if dep not in info:
            print(f"FAIL deb: GUI runtime dep missing: {dep}")
            ok = False
    # wrong-arch multilib leaks and unversioned toolchain floors
    # are both real install bugs - the download-back loop caught
    # each once already.
    if "libc6-i386" in info or "libc6-amd64" in info:
        print("FAIL deb: wrong-arch multilib package in Depends")
        ok = False
    if "libc6 (>= " not in info:
        print("FAIL deb: libc6 floor not versioned")
        ok = False
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["dpkg-deb", "-x", str(deb), td],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("FAIL deb: dpkg-deb -x:", r.stderr)
            return False
        root = Path(td)
        binary = root / "usr" / "local" / "bin" / "las-desktop"
        for f in (binary,
                  root / "usr" / "share" / "applications"
                  / f"{RDNS}.desktop",
                  root / "usr" / "share" / "icons" / "hicolor"
                  / "128x128" / "apps" / "local-agent-studio.png",
                  root / "usr" / "share" / "doc"
                  / "local-agent-studio" / "copyright"):
            if not f.exists():
                print(f"FAIL deb: payload missing: {f.name}")
                ok = False
        if binary.exists():
            binary.chmod(0o755)
            v = smoke(binary)
            print(f"deb execution smoke: {v}")
            ok = ok and v.startswith(("OK", "SKIP"))
    print("deb:", "OK" if ok else "FAILED")
    return ok


def verify_appimage(img: Path) -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([str(img), "--appimage-extract"],
                           cwd=td, capture_output=True, text=True)
        root = Path(td) / "squashfs-root"
        if r.returncode != 0 or not root.exists():
            print("FAIL appimage: self-extraction failed:", r.stderr)
            return False
        binary = root / "usr" / "bin" / "las-desktop"
        for f in (binary, root / "AppRun",
                  root / f"{RDNS}.desktop",
                  root / "usr" / "share" / "metainfo"
                  / "io.github.kimpearce888.local-agent-studio.appdata.xml"):
            if not f.exists():
                print(f"FAIL appimage: payload missing: {f.name}")
                ok = False
        if binary.exists():
            binary.chmod(0o755)
            v = smoke(binary)
            print(f"appimage execution smoke: {v}")
            ok = ok and v.startswith(("OK", "SKIP"))
    print("appimage:", "OK" if ok else "FAILED")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    outdir = Path("/tmp/ciart")
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True)
    download_artifacts(args.run_id, token(), outdir)

    debs = sorted(outdir.glob("*.deb"))
    imgs = sorted(outdir.glob("*.AppImage"))
    if not debs or not imgs:
        print("FAIL: expected one .deb and one .AppImage, got",
              [p.name for p in outdir.iterdir()])
        return 1
    ok = verify_deb(debs[0])
    ok = verify_appimage(imgs[0]) and ok
    if ok:
        for p in [*debs, *imgs]:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            print(f"sha256 {p.name}: {h}")
    if not args.keep:
        pass    # keep /tmp/ciart for inspection either way; small
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
