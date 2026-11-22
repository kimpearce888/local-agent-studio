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

- `local-agent-studio_0.4.0_amd64.deb` - dpkg archive; verified by
  unpacking and checking the payload tree, the desktop entry and the
  binary (`dpkg-deb -x` plus checksum comparison). Installing needs
  root on the target machine.
- `local-agent-studio-0.4.0-x86_64.AppImage` - built with
  appimagetool from the extracted tool (no root, no FUSE needed to
  build); verified by self-extraction (`--appimage-extract`) and
  payload comparison. Running it needs FUSE on the target machine, or
  `--appimage-extract` once as a fallback.

Both artifacts live in `dist/` and are rebuilt by
`scripts/make_appimage.py` (AppImage) and the packaging script in the
same directory (deb). Version strings are unified at 0.4.0 across the
Cargo workspace, `pyproject.toml`, `las.__version__` and the meta
table's `app_version` row, so an artifact version always matches what
`studio --version` reports.

CI (`.github/workflows/ci.yml`) gates every push on the Python matrix
(3.10/3.11/3.12): the full unittest suite, the disposable-module
removal verifications and a packaging smoke test (wheel builds,
installs, `studio --version` runs). The suite skips the tkinter GUI
tests honestly when the Python build ships without _tkinter or no
DISPLAY is available - it never collects errors for environmental
reasons.
