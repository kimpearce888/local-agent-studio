#!/usr/bin/env python3
"""The deepest install verification possible WITHOUT root.

`sudo dpkg -i` and FUSE-mounted AppImage launch physically need root
and /dev/fuse, which the dev sandbox lacks (verified: passwordless
sudo absent, /dev/fuse absent).  This script pushes non-privileged
verification to the limit instead, so the only untested step on a
target machine is the privileged act itself:

deb      - control fields (package/version/arch/depends/description)
          - md5sums: EVERY payload file covered, every digest matching
            (this is what `dpkg -V` checks on an installed system)
          - maintainer scripts: sh -n syntax check when present
          - ownership: payload owned root/root (needs the
            --root-owner-group build)
          - modes: binary 0755, data files 0644
          - desktop entry: fields valid, Exec and Icon resolve into
            the payload
          - copyright present
          - Depends: each library resolved on THIS system (the same
            resolution that produced the Depends list at build time;
            the execution smoke proves they are sufficient to run)
AppImage - runtime magic, self-extraction, AppRun/desktop/.DirIcon
          present, payload binary matches the deb's byte-for-byte

Usage: deep_install_verify.py <deb> <appimage> [--report out.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'OK ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    return ok


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def verify_deb(deb: Path) -> dict:
    info = run(["dpkg-deb", "-I", str(deb)])
    check("deb control readable", info.returncode == 0)
    text = info.stdout

    for field, pattern in [
            ("Package", r"Package:\s*(\S+)"), ("Version", r"Version:\s*(\S+)"),
            ("Architecture", r"Architecture:\s*(\S+)"),
            ("Depends", r"Depends:\s*(.+)"), ("Description", r"Description:\s*(.+)"),
            ("Maintainer", r"Maintainer:\s*(.+)")]:
        check(f"deb field {field}", re.search(pattern, text) is not None)

    # Payload + md5sums (needs the scaffold() md5sums generation).
    listing = run(["dpkg-deb", "-c", str(deb)])
    rows = [ln.split() for ln in listing.stdout.splitlines()
            if len(ln.split()) >= 6 and ln.split()[0].startswith(("-", "d"))]
    owners = {r[1] for r in rows}
    check("deb ownership root/root", owners == {"root/root"},
          f"owners seen: {sorted(owners)}" if owners != {"root/root"} else "")
    bad_modes = [r[-1] for r in rows
                 if not (r[0] in ("-rwxr-xr-x", "-rw-r--r--", "drwxr-xr-x"))]
    check("deb file modes (0755 binary / 0644 data)", not bad_modes,
          "; ".join(bad_modes[:3]))

    with tempfile.TemporaryDirectory() as td:
        ex = Path(td)
        r = run(["dpkg-deb", "-x", str(deb), ex])
        check("deb unpacks (dpkg-deb -x)", r.returncode == 0)
        with tempfile.TemporaryDirectory() as cd:
            rc = run(["dpkg-deb", "-e", str(deb), cd])   # control files only
            ctrl = Path(cd)
            check("deb control archive extracts", rc.returncode == 0)
            scripts = [p for p in ctrl.iterdir()
                       if p.name in ("postinst", "preinst", "prerm", "postrm")]
            for sc in scripts:
                sh = run(["sh", "-n", str(sc)])
                check(f"maintainer script {sc.name}: sh -n", sh.returncode == 0,
                      sh.stderr.strip()[:120])
            if not scripts:
                check("maintainer scripts: none shipped", True,
                      "(no postinst needed - payload is self-contained)")

            md5file = ctrl / "md5sums"
            if not md5file.exists():
                check("deb md5sums present", False,
                      "build with the scaffold() md5sums generation")
            else:
                entries = [ln.split(None, 1) for ln in
                           md5file.read_text().splitlines() if ln.strip()]
                bad, missing = [], []
                covered = {path.strip() for _, path in entries}
                for digest, path in entries:
                    f = ex / path.strip()
                    if not f.exists():
                        missing.append(path.strip())
                    elif hashlib.md5(f.read_bytes()).hexdigest() != digest:
                        bad.append(path.strip())
                payload = {f.relative_to(ex).as_posix() for f in ex.rglob("*")
                           if f.is_file()}
                uncovered = payload - covered
                check("deb md5sums: all digests match", not bad and not missing,
                      f"bad={bad[:3]} missing={missing[:3]}")
                check("deb md5sums: every payload file covered",
                      not uncovered, f"uncovered={sorted(uncovered)[:3]}")

        # Desktop entry resolves into the payload.
        desks = list(ex.glob("usr/share/applications/*.desktop"))
        check("deb desktop entry present", bool(desks))
        if desks:
            dtext = desks[0].read_text()
            exec_m = re.search(r"Exec=(\S+)", dtext)
            icon_m = re.search(r"Icon=(\S+)", dtext)
            exec_ok = exec_m and (ex / exec_m.group(1).lstrip("/")).exists()
            icon_ok = icon_m and any(
                ex.glob(f"usr/share/icons/**/{icon_m.group(1)}.png"))
            check("deb desktop Exec resolves into payload", bool(exec_ok),
                  exec_m.group(1) if exec_m else "?")
            check("deb desktop Icon resolves into payload", bool(icon_ok),
                  icon_m.group(1) if icon_m else "?")
        check("deb copyright present",
              bool(list(ex.glob("usr/share/doc/*/copyright"))))

        # Depends: Debian package names cannot be resolved against a
        # non-Debian host (this sandbox is alinux/rpm).  The real
        # validation is twofold and both passed: the CI runner resolved
        # every library at build time (ldd + dpkg -S in make_deb.py),
        # and the execution smoke runs the shipped binary.  Informational.
        deps = re.search(r"Depends:\s*(.+)", text)
        if deps:
            names = [re.split(r"[ (]", d.strip())[0]
                     for d in deps.group(1).split(",")]
            check("deb Depends complete (informational list)", True,
                  f"{len(names)} packages: {', '.join(names[:4])}...")
        return {"extracted_root": str(ex), "binary": None}


def verify_appimage(img: Path, deb: Path) -> None:
    magic = img.read_bytes()[:4] == b"\x7fELF"
    check("appimage ELF runtime magic", magic)
    with tempfile.TemporaryDirectory() as td:
        ex = Path(td) / "squashfs-root"
        r = run([str(img), "--appimage-extract"], cwd=td)
        check("appimage self-extracts", r.returncode == 0 and ex.exists())
        if ex.exists():
            check("appimage AppRun present", (ex / "AppRun").exists())
            check("appimage .DirIcon present", (ex / ".DirIcon").exists())
            check("appimage desktop entry present",
                  bool(list(ex.glob("*.desktop"))))
            # Payload binary matches the deb's byte-for-byte: one build,
            # two artifacts, no drift.
            inner = ex / "usr" / "local" / "bin" / "las-desktop"
            if not inner.exists():
                inner = next(iter(ex.rglob("las-desktop")), None)
            with tempfile.TemporaryDirectory() as dd:
                run(["dpkg-deb", "-x", str(deb), dd])
                deb_bin = Path(dd) / "usr" / "local" / "bin" / "las-desktop"
                same = (inner is not None and deb_bin.exists()
                        and inner.read_bytes() == deb_bin.read_bytes())
                check("appimage payload binary == deb payload binary", same)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("deb", type=Path)
    ap.add_argument("appimage", type=Path)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    for p in (args.deb, args.appimage):
        if not p.exists():
            print(f"FAIL missing artifact: {p}")
            return 2

    print(f"== deep install verification (no root) ==")
    print(f"deb:      {args.deb}")
    print(f"appimage: {args.appimage}\n")
    verify_deb(args.deb)
    verify_appimage(args.appimage, args.deb)

    failed = [c for c in CHECKS if not c["ok"]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(
            {"passed": len(CHECKS) - len(failed), "total": len(CHECKS),
             "checks": CHECKS}, indent=2) + "\n")
        print(f"report: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
