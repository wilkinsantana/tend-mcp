# TEND MCP contributor guide

Read `ROADMAP.md` before development. TEND MCP is an independent Tend component;
the current standalone Python package is a prototype, not a production component.
Core owns authentication, authorization, resource restrictions, ingress, lifecycle
and audit. Do not import companion source, share databases or credentials, expose
an unauthenticated MCP HTTP service, or execute arbitrary extension backend code.

## Working policy

- Start with `git status --short`; preserve unrelated changes.
- Discover narrowly with `rg`; load only relevant files and skills.
- Make one independently testable change at a time. Run focused local checks;
  Gitea owns the complete validation suite. Avoid duplicate full-suite runs.
- Keep outputs bounded and redact private data. Credentials stay in approved
  secret stores, never chat, argv, source, examples, logs, or Git remotes.
- Default to one agent; independent security review remains a release gate.
  Do not simulate review or silently change the user's model/provider.
- Preserve compaction and record concise handoffs with exact evidence and risks.
- Update this repository's roadmap when a contract, gate, or status changes.

## Validation and publication

- Local checks: `uv run pytest -q`, `uv run ruff check src tests`,
  `uv run ruff format --check src tests`, `uv run mypy src`.
  Publisher changes additionally include `scripts/sign-runtime-release.py` in
  Ruff and mypy targets; see `docs/runtime-release-publishing-v1.md`. Gitea
  includes these checks but performs no production component signing.
- Gitea `.gitea/workflows/ci.yml` is the CI authority. Verify branch triggers and
  runner availability rather than assuming a push started validation.
- GitHub receives only the exact passing SHA through the workflow's publication
  job. Never manually bypass that gate or force-push to repair publication.
- The current publisher uses HTTPS Git and `GH_PUBLISH_TOKEN`. A local GitHub
  login, a Gitea API token, or another repository's deploy key is not equivalent.
- `scripts/check-publication-access.py` emits status-only credential diagnostics
  inside CI. Never print the token or GitHub response bodies. Read access does
  not prove write access; verify the publisher result and remote SHA.
- Source mirroring is not PyPI publication, a signed component artifact, catalog
  activation, or deployment. Each has separate gates in `ROADMAP.md`.
- Before staging: inspect exact files, run `git diff --check`, scan staged content
  for secrets and private connection/local-path details. Keep runtime/generated
  data and unrelated edits out of commits.

## Cross-repository work

Locate companion repositories from the active workspace; do not store a local
checkout path here. Use Tend core's release skill and its generic Gitea
publication reference when available. Keep each repository's changes and
publication separate. Exchange contract version, capability/endpoint changes,
compatibility, security assumptions, exact test/CI evidence, and pending gates.
