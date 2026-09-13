# TEND MCP component roadmap

## Canonical direction

TEND MCP is an independent optional component in Tend's existing catalog,
alongside Notes and Sites. Proposed identity: `host.tend.mcp`. It has its own
repository and signed releases, installation, enable/disable, updates, rollback,
and uninstall. Standalone-first publication is superseded.

The existing Python package is an unreviewed prototype, not a finished component.
Do not expose its HTTP transport publicly or publish it as a ready component.
Mock-panel tests do not establish compatibility or security with a real panel.

Panel-owned contracts (locate the tend.host checkout from the active workspace):
- `docs/strategy/tend-mcp-component-v1.md`: accepted product direction.
- `docs/strategy/tend-mcp-runtime-contract-v1.md`: proposed runtime contract;
  unresolved isolation, artifact, enrollment, and grant details block freezing.
- `Roadmap/AGENT-ACCESS-ROADMAP.md`: shared implementation gates.

## Gates

- [x] C0: Record independent component direction in both repositories.
- [ ] C1: Review/freeze the runtime contract. Draft exists in panel repository.
- [ ] C2: Panel admission, supervision, epoch fencing, and lifecycle recovery.
  Core commit `8ca3714e` adds the inactive `mcp_component_lifecycle.py` ledger:
  atomic full-snapshot comparisons, startup/stop epochs, stopped-only update and
  rollback, restart fencing, and reinstall tombstones. Focused tests: 19 passed;
  independent review approved this inactive slice only. No supervisor, artifact
  trust, ingress, or client authorization is activated by the ledger.
  Core `6a575b9e` adds inactive signed runtime metadata verification and durable
  per-platform sequence floors. Runtime envelope schema 1 uses Ed25519 domain
  `tend-mcp-runtime-release-v1`, externally pinned keys, stable version/core
  ranges, `docker-isolated-stdio-v1`, exact UI/service SHA-256 bindings, and a
  read-only capability vocabulary. The full wire contract is in the panel's
  `docs/strategy/tend-mcp-runtime-contract-v1.md`. Same-version mutation,
  downgrade, expiry, revoked trust, malformed input and failed persistence are
  rejected. Focused runtime/lifecycle/Notes-discovery suite: 90 passed;
  independent read-only review approved the metadata slice only. Publisher
  signing/provisioning, artifact transport/byte verification and runtime
  activation remain dependencies; no shared source import is authorized.
- [ ] C3: Authenticated ingress, per-client resource grants and revocation.
- [ ] C4: Component UI/service artifacts and existing catalog/update integration.
- [ ] C5: Bounded redacted read tools tested against real panel contracts.
- [ ] C6: Separately reviewed writes/jobs and backend-enforced human approvals.
- [ ] C7: Independent review, disposable lifecycle evidence per supported mode,
      Gitea validation, and exact-commit/artifact publication.

## Ownership and handoff

Core owns authentication, authorization, resource restrictions, audit, ingress,
lifecycle ledger and typed dispatch. This component owns MCP translation,
curated tools, resources/prompts and its UI. No core source imports, shared
mutable databases, broad owner tokens, or arbitrary backend extension execution.
Exchange contract version, identifiers/capabilities, compatibility, test evidence,
and pending dependency in each cross-repo handoff.

## Current blockers

The prototype has not passed the component contract gates. Native isolation and
remote client authorization are unresolved. Tool payloads, redaction, transport
security, durable operation behavior and destructive approvals need review.
Gitea auto-publication is configured and the GitHub mirror reached verified
commit `aed7bfd3339c0f0b83efe4f19e63ec3c40c3c7df`. Never bypass the exact-SHA
validation gate or treat source publication as a component release.

## Prototype safety remediation

- HTTP CLI serving is disabled pending authenticated core ingress. Local stdio
  remains a development-only transport; it is not component installation.
- CLI refusal tests plus existing tool tests: `uv run pytest -q` → 15 passed;
  `uv run ruff check src tests` and `uv run mypy src` pass.
- Independent read-only review approved the HTTP refusal slice only; this is
  not full runtime or component security approval.
- Core separately rejects local PATs in hosted mode and denies token access to
  decrypted database connection strings and arbitrary host file contents.
