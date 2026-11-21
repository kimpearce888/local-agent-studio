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
