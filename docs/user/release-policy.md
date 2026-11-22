# Release channels and support policy

This is the engine repository's published policy (DST-4), following the
product requirements document, section 19. The desktop packaging phase
puts it into effect for signed installers and updates.

## Channels

- **Stable**: every general-availability release. Staged rollout; a
  one-click rollback to the previous signed release.
- **Beta**: early access of release candidates. Automatic updates are
  opt-in and off by default (SET-4); the update manifest is signed and
  carries version, URL and hash, and the client rejects downgrades and
  unsigned manifests.

Documentation, changelog and migration notes are release gates, not
follow-ups. Data migrations back up before they run (the engine already
writes `<data>/backups/` before schema changes).

## Support

- Bug fixes: the current and the previous minor release.
- Security fixes: at least five years after each release when
  distributed commercially in the EU, per the product requirements
  document's support policy.
- Vulnerability handling: a security contact and disclosure policy are
  published with the first commercial distribution, with a named incident
  owner, reporting under the Cyber Resilience Act.

## Compatibility watch

LM Studio, Unsloth Studio, llama.cpp, Open Responses and MCP releases are
checked monthly; adapter updates ship in patch releases. Preset defaults
in the engine carry the last-verified vendor documentation date.

## What is release-blocking

- Any regression in the product requirements document's success metrics
  (section 17) on the 100-goal benchmark.
- Any red test in the engine suite (`python3 -m unittest discover -s tests`)
  or any failed module-removal verification (`tools/removal_test.py`).
- A missing or stale changelog or migration note.

Nightly contract tests against real local servers (LM Studio, Unsloth
Studio, Ollama, llama.cpp) run outside this repository and gate stable
releases; they are listed as open work until a machine with those servers
is available (see PROGRESS.md).

## Desktop artifacts (0.4.0)

The Rust desktop ships as two Linux artifacts, both built from the
same release binary and both verified structurally in the build
pipeline (never by claim):

- `local-agent-studio_0.4.0_amd64.deb` - dpkg archive, scaffolded
  ENTIRELY by `scripts/make_deb.py` (control file whose Depends are
  derived from the binary itself - the linked libraries via ldd,
  plus the dlopen'd GUI runtime set spelled out: libx11-6,
  libxcursor1, libxrandr2, libxi6, libxkbcommon0,
  libxkbcommon-x11-0, libxcb-xkb1, libegl1, libvulkan1 - desktop
  entry, generated icon, copyright).  Verified three ways, all
  without root: structurally (`dpkg-deb -x` plus payload
  checksums), by metadata (`dpkg-deb -I` control fields), and by
  EXECUTION - the unpacked binary is started on a private Xvfb
  display with software GL and must run a real GUI main loop for
  three seconds while opening its SQLite store.  Only `dpkg -i`
  itself still needs root on the target machine.
- `local-agent-studio-0.4.0-x86_64.AppImage` - built with
  appimagetool from the extracted tool (no root, no FUSE needed to
  build), with the AppDir (AppRun, desktop entry, icon, AppStream
  metainfo) scaffolded by `scripts/make_appimage.py` from any fresh
  checkout; verified by self-extraction (`--appimage-extract`),
  payload checksums, and the same execution smoke on the extracted
  payload. Running it needs FUSE on the target machine, or
  `--appimage-extract` once as a fallback.

Both artifacts live in `dist/` and are rebuilt by
`scripts/make_deb.py` (deb) and `scripts/make_appimage.py`
(AppImage); on a headless build host missing one of the dlopen'd
GUI libraries, extract the distro package and point
`LAS_SMOKE_LIBDIR` at it - the smoke then mirrors exactly what the
installed dependencies satisfy on a real desktop. Version strings
are unified at 0.4.0 across the
Cargo workspace, `pyproject.toml`, `las.__version__` and the meta
table's `app_version` row, so an artifact version always matches what
`studio --version` reports.

CI (`.github/workflows/ci.yml`) gates every push on two jobs.  The
Python matrix (3.10/3.11/3.12) runs the full unittest suite, the
disposable-module removal verifications and a packaging smoke test
(wheel builds, installs, `studio --version` runs).  The Rust job
builds the workspace on stable: `cargo test --workspace --locked`
(engine, parity, acceptance and the headless egui_kittest UI suite)
plus a release build of the desktop shell. The suite skips the tkinter GUI
tests honestly when the Python build ships without _tkinter or no
DISPLAY is available - it never collects errors for environmental
reasons.
