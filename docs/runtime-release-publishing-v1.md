# Runtime-release-v1 publisher (inactive delivery lane)

The offline `scripts/sign-runtime-release.py` produces the exact schema-1
metadata consumed by the panel's `core/mcp_runtime_release.py`. It does not
build, upload, install or execute a component, configure host trust, or certify
an artifact. Production component release remains gated by `ROADMAP.md`.

## Inputs and secret boundary

Run only against reviewed, immutable outputs in a trusted publisher workspace.
The signing key belongs solely in the publisher's approved CI secret store as
`COMPONENT_RELEASE_SIGNING_KEY`: canonical padded base64 of 32 raw Ed25519
private-key bytes. Do not pass it in argv, create a plaintext key file, place it
in an artifact, print it, or reuse a panel credential or another component's key.
This script neither generates keys nor provisions CI secrets. The host public
key must be pinned separately through core-owned reviewed policy.

The release dependency group contains `cryptography`; it is not added to the
MCP runtime's direct dependencies. Development checks include that group.

```sh
uv run --group release python scripts/sign-runtime-release.py \
  --ui-artifact build/mcp-ui.zip \
  --service-artifact build/mcp-service.artifact \
  < build/runtime-metadata.json > build/runtime-release.json
```

Paths above are illustrative build outputs, not production artifacts currently
produced by this repository. The offline service container packager is described
in [service-package-v1.md](service-package-v1.md); it requires a pre-existing
normalized OCI layout and does not build the service image. A failed command must block upload; do not treat an empty or
partially written output as a release. Existing Gitea CI tests the publisher but
never signs/releases components or obtains a production signing key.

The stdin metadata is bounded to 16 KiB, contains exactly these public fields,
and must contain no credentials or commands:

| Field | Contract |
|---|---|
| `version` | Canonical stable `major.minor.patch`; each number at most six digits |
| `issued_at`, `expires_at` | Integer Unix seconds; current time inside the half-open window; at most 365 days |
| `min_core_version`, `max_core_version` | Canonical stable versions; inclusive ordered compatibility range |
| `platform` | `linux/amd64` or `linux/arm64` |
| `capabilities` | Nonempty sorted unique subset of `mcp.apps.summary.read`, `mcp.deployments.status.read`, `mcp.servers.health.read` |

Compatibility and capability claims require actual acceptance evidence before
production signing. This tool validates their shape, not their implementation.

The publisher fixes `component_id=host.tend.mcp` and
`runtime_profile=docker-isolated-stdio-v1`, derives the sequence as
`major * 10^12 + minor * 10^6 + patch`, and hashes both actual artifacts. It
refuses empty/nonregular/oversized files, leaf symlinks, and detectable changes
during reading. Publisher platforms lacking no-follow/nonblocking open fail
closed. Parent directories belong to the trusted CI workspace; this is not a
general filesystem sandbox. Keep build outputs immutable through upload; core
must independently recheck all received bytes.

## Wire contract and byte identity

`ui_sha256` and `service_sha256` bind the exact nonempty **transport-file
bytes**, not an unpacked tree or a separately retrieved OCI manifest. Bounds are
20 MiB for UI and 512 MiB for service bytes. Archive format, safe extraction,
image identity, platform contents and confinement are separate pending gates;
this script can sign inert test bytes and does not prove a valid Docker service.

The envelope has exactly `schema=1`, `key_id`, `payload`, and `signature`.
`key_id` is the first 16 lowercase hex characters of SHA-256 over the raw public
key. Signature input is ASCII `tend-mcp-runtime-release-v1` plus newline plus
canonical payload JSON (sorted keys, compact separators, ASCII escaping).
Signature encoding is canonical padded base64 of 64 bytes. No public or private
key is embedded in the envelope. Core must independently trust the key ID,
verify the signature and compatibility, and commit its platform sequence floor.

A same-version payload is immutable, including its validity window. Republishing
changed claims under the same version fails at core; never work around this by
resetting the floor. Key rotation may sign the exact same payload after the new
public key is separately trusted. Expired metadata cannot admit a fresh install.

## Checks and interoperability

```sh
uv run pytest -q tests/test_release_publisher.py
uv run ruff check scripts/sign-runtime-release.py tests/test_release_publisher.py
uv run ruff format --check scripts/sign-runtime-release.py tests/test_release_publisher.py
uv run mypy scripts/sign-runtime-release.py
```

The panel retains a public, test-only signed fixture produced by this CLI in
`backend/tests/fixtures/mcp-runtime-release-v1.json`. Its artifact contents are
inert examples, and no private key is retained. Core's
`test_mcp_artifact_integrity.py` independently verifies the signature and actual
streamed bytes, and rejects unpinned keys, changed/truncated content, size
violations, stalled transfers and cancellation. This is wire interoperability,
not installed-component lifecycle evidence. Neither repository imports the
other's source or shares mutable runtime state.
