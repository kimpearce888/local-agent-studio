#!/usr/bin/env python3
"""Build and verify the .deb artifact (release-policy.md).

Rebuilds dist/local-agent-studio_<ver>_amd64.deb from the release
binary.  The whole payload tree is scaffolded HERE - control file,
desktop entry, icon and copyright - so the script works from any
fresh checkout (no pre-assembled dist/ required).

Verification, in order (never claims success without all of it):
1. STRUCTURAL: dpkg-deb -x unpacks the archive; the payload
   checksums match what was staged.
2. METADATA: dpkg-deb -I shows the control fields we wrote.
3. EXECUTION (when xvfb-run exists): the UNPACKED binary is started
   under a virtual X display with a private data dir; it must stay
   alive for 3 seconds (a real GUI main loop, not an instant crash)
   and create its SQLite store.

Installing with `dpkg -i` still needs root on a target machine -
everything short of that is verified here.

Usage: python3 scripts/make_deb.py [--skip-build]
"""
import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST = ROOT / "rust"
DIST = ROOT / "dist"
DEB_TREE = DIST / "deb"
BIN_SRC = RUST / "target" / "release" / "las-desktop"
BIN_DST = DEB_TREE / "usr" / "local" / "bin" / "las-desktop"

DESC = ("A local-first desktop studio for building, running and "
        "supervising teams of AI agents - engine, tools, MCP and "
        "sessions live on your machine.")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def workspace_version() -> str | None:
    for line in (RUST / "Cargo.toml").read_text().splitlines():
        if line.startswith("version ="):
            return line.split("=")[1].strip().strip('"')
    return None


def shared_depends(binary: Path) -> str:
    """Runtime dependencies as a Depends list.  ldd + dpkg -S derive
    the LINKED libraries (this binary is static except libc/libm/
    libgcc); winit dlopens the X11 client set at runtime, so that
    documented GUI runtime is appended - and the execution smoke
    below proves the whole set really suffices to boot the GUI."""
    pkgs: list[str] = []
    r = subprocess.run(["ldd", str(binary)],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        lib = line.strip().split("=>")
        if len(lib) < 2 or "not found" in line:
            continue
        so = lib[0].strip()
        q = subprocess.run(["dpkg", "-S", so],
                           capture_output=True, text=True)
        if q.returncode == 0 and ":" in q.stdout:
            # Trim the multiarch qualifier dpkg reports.
            pkg = q.stdout.split(":")[0].strip()
            if pkg not in pkgs:
                pkgs.append(pkg)
    # The dlopen'd set (winit X11 + xkbcommon-x11 + wgpu's EGL and
    # Vulkan loaders); libxcb-xkb1 rides along with
    # libxkbcommon-x11-0 but is listed for completeness.
    gui_runtime = ["libx11-6", "libxcursor1", "libxrandr2",
                   "libxi6", "libxkbcommon0", "libxkbcommon-x11-0",
                   "libxcb-xkb1", "libegl1", "libvulkan1"]
    for pkg in gui_runtime:
        if pkg not in pkgs:
            pkgs.append(pkg)
    return ", ".join(pkgs)


def icon_png(size: int = 128) -> bytes:
    """A deterministic stdlib-only PNG: dark rounded mark with a
    lighter inner square (the studio 'hub' motif)."""
    a, b, c = (24, 28, 38), (58, 122, 159), (235, 240, 244)
    rows = []
    for y in range(size):
        row = bytearray([0])   # filter byte per scanline
        for x in range(size):
            m = (18 <= x < size - 18) and (18 <= y < size - 18)
            inner = (40 <= x < size - 40) and (40 <= y < size - 40)
            dot = (size - 52 <= x < size - 36) and (18 <= y < 34)
            if dot or inner:
                rgb = c
            elif m:
                rgb = b
            else:
                rgb = a
            row += bytes(rgb)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data)
                                 & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def scaffold(version: str, depends: str) -> None:
    """Create the full payload tree (idempotent - regenerated every
    run, so the archive always matches this source)."""
    if DEB_TREE.exists():
        shutil.rmtree(DEB_TREE)
    # Binary + docs.
    BIN_DST.parent.mkdir(parents=True)
    shutil.copy2(BIN_SRC, BIN_DST)
    doc = DEB_TREE / "usr" / "share" / "doc" / "local-agent-studio"
    doc.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", doc / "copyright")
    # Desktop entry + icon.
    apps = DEB_TREE / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "local-agent-studio.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Local Agent Studio\n"
        f"Comment={DESC}\n"
        "Exec=/usr/local/bin/las-desktop\n"
        "Terminal=false\n"
        "Categories=Development;\n"
        "Icon=local-agent-studio\n")
    icons = DEB_TREE / "usr" / "share" / "icons" / "hicolor" \
        / "128x128" / "apps"
    icons.mkdir(parents=True)
    (icons / "local-agent-studio.png").write_bytes(icon_png())
    # Control.
    ctrl = DEB_TREE / "DEBIAN"
    ctrl.mkdir(parents=True)
    (ctrl / "control").write_text(
        "Package: local-agent-studio\n"
        f"Version: {version}\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Architecture: amd64\n"
        f"Depends: {depends}\n"
        "Maintainer: Local Agent Studio project\n"
        f"Description: {DESC}\n")


def execution_smoke(unpacked_root: Path) -> str:
    """Run the UNPACKED binary on a private Xvfb display; return a
    verdict line.  Skipped (plainly) when Xvfb is absent.  A build
    machine missing one of the dlopen'd GUI libs (they are not in
    ldd) can point LAS_SMOKE_LIBDIR at an extracted distro package
    - mirrors what `dpkg -i` satisfies on a real target."""
    import os
    import time
    if shutil.which("Xvfb") is None:
        return "SKIP: execution smoke (no Xvfb on this machine)"
    display = f":{os.getpid() % 400 + 20}"
    sock = Path(f"/tmp/.X11-unix/X{display[1:]}")
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):   # wait for the display socket
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
                   # Xvfb has no GPU: force Mesa's software
                   # rasterizer so the wgpu surface can be created.
                   "LIBGL_ALWAYS_SOFTWARE": "1"}
            extra = os.environ.get("LAS_SMOKE_LIBDIR", "")
            if extra:
                env["LD_LIBRARY_PATH"] = (
                    f"{extra}:{env.get('LD_LIBRARY_PATH', '')}")
                # glvnd needs a vendor json it can find; when the
                # distro packages are only EXTRACTED (not
                # installed), point at theirs directly.
                vendor = (Path(extra).parents[1] / "share"
                          / "glvnd" / "egl_vendor.d"
                          / "50_mesa.json")
                if vendor.exists():
                    env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor)
            p = subprocess.Popen(
                [str(unpacked_root / "usr" / "local" / "bin"
                     / "las-desktop")],
                env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # A real GUI main loop keeps running; an instant crash
            # exits within a second.
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
            return (f"OK: binary ran 3s on display {display} "
                    "and opened its store")
    finally:
        xvfb.terminate()
        xvfb.wait()


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

    version = workspace_version()
    if not version:
        print("FAIL: workspace version not found")
        return 1

    print(f"version: {version}")
    depends = shared_depends(BIN_SRC)
    print(f"depends (from ldd + dpkg -S): {depends or '(none)'}")
    scaffold(version, depends or "libc6")
    binary_sha = sha256(BIN_DST)

    out = DIST / f"local-agent-studio_{version}_amd64.deb"
    print(f"dpkg-deb --build -> {out.name}")
    r = subprocess.run(
        ["dpkg-deb", "--build", str(DEB_TREE), str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL: dpkg-deb failed:", r.stderr)
        return 1

    # (1) STRUCTURAL: unpack and compare.
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
        # (2) METADATA.
        r = subprocess.run(["dpkg-deb", "-I", str(out)],
                           capture_output=True, text=True)
        info = r.stdout
        for field in (f"Package: local-agent-studio",
                      f"Version: {version}",
                      "Architecture: amd64"):
            if field not in info:
                print(f"FAIL: control field missing: {field}")
                return 1
        # (3) EXECUTION (when possible on this machine).
        verdict = execution_smoke(Path(td))
        print(verdict)
        if verdict.startswith("FAIL"):
            return 1

    print(f"OK: {out} ({out.stat().st_size} bytes) - unpack + "
          f"checksum + metadata verified, {verdict.split(':')[0]}"
          " execution smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
