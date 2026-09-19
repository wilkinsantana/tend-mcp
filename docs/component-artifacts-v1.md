# Component artifacts v0.1.0 — prepared, inactive

Core-facing fixed release tag: **`component-v0.1.0`**, repository
`wilkinsantana/tend-mcp`. Exact assets:

- `tend-mcp-ui.zip`
- `service-linux-amd64.zip`
- `service-linux-arm64.zip`
- `runtime-linux-amd64.json`
- `runtime-linux-arm64.json`

The runtime envelopes retain schema 1 and `docker-isolated-stdio-v1`, sequence
1000000 for version 0.1.0, exact transport hashes, independently pinned Ed25519
trust, and inclusive core range 0.1.0–0.1.0. Capabilities are exactly
`mcp.apps.summary.read` and `mcp.deployments.status.read`. First publication fixes
the validity window to 180 days. No frozen contract field or naming conflict was
found: earlier scripts accept output paths and stable semantic versions.
Same-version changed payloads remain prohibited, including expiry refresh.

## Gitea lane

The existing mandatory test/worker-image/core lifecycle source gate and automatic
GitHub main mirroring remain intact. Source mirroring is not this release.
Only a main push whose exact SHA equals the repository variable
`COMPONENT_RELEASE_APPROVED_SHA` can enter the additional release graph. Leave
that variable unset until parent integration and all gates below are complete.
No PR/manual-dispatch signing, GitHub CI, tag-trigger workaround, local signing,
key generation, secret inspection or upload is needed to develop this lane.

1. `component_build` uses native `tend-ci-amd64` and `tend-ci-arm64` runners. These
   labels are requirements, **not evidence of provisioned/available runners**.
   Each reuses the existing pinned Dockerfile, image normalizer, isolated probe
   and deterministic service ZIP packager. The amd64 job packages the native UI.
   These are validation builds; there is no rebuild in the signing/upload job.
2. `component_identity` consumes those exact same-run outputs after mandatory
   worker acceptance, verifies each named service platform and UI identity, and
   records bounded transport SHA-256 hashes plus immutable source SHA. The receipt
   stays in Gitea, not in the signed wire contract or public asset set.
3. `component_publish` waits for the source tests, worker lifecycle gate, artifact
   identity and exact-SHA source publisher. It requires a clean exact checkout,
   approved source SHA and that SHA at GitHub main. It downloads already built
   assets, rechecks the receipt and signs their actual unchanged bytes using the
   existing schema-1 signer. Only this job receives the production signing key.
4. The publisher creates a new exact-SHA tag, draft release, and five assets
   without clobber. It downloads the draft assets, checks the exact member set
   and hashes, then publishes the draft. All remote/subprocess failures are
   status-only. No production deployment or host trust change occurs.

The signing key uses the existing `COMPONENT_RELEASE_SIGNING_KEY` approved CI
secret. `GH_PUBLISH_TOKEN` is mapped to `GH_TOKEN` only for the GitHub CLI. The
publisher removes the signing key from Git/gh child environments and removes the
GitHub token from the signer environment. CI runners require `gh`, Python 3.12,
Docker, native platform execution and working Gitea upload/download-artifact v3
actions. No action/runner availability is inferred from this preparation.

## Failure and identity policy

Do not amend/rebuild artifacts between validation and publication. The native
ZIP is deterministic from module bytes; the service ZIP is deterministic from
its normalized OCI layout. Independently repeated Docker builds are **not**
claimed bit-reproducible. Retain the exact validated artifacts and receipt.
No tag/release/asset is replaced on rerun. A partial tag/draft/upload failure
blocks automation and requires explicit owner reconciliation preserving the
original signed envelopes and bytes. Never rerun to refresh a same-version
validity window or silently delete a previous release. The download verification
is a trusted fixed-repository publisher check, not an adversarial artifact
transport sandbox; core still performs bounded independent admission checks.

## Pending activation gates

- Parent's final exact green/public core pin and public-read integration handoff;
  current mandatory worker fixture pin is intentionally unchanged here.
- Actual native UI scanner/managed-install/browser accessibility and assistant
  enrollment/read/denied-sibling/revoke acceptance with these UI bytes.
- Native amd64/arm64 runners and both platform lifecycle acceptance. Current
  mandatory core fixture runs amd64 direct/loopback SSH only. Do not infer arm64
  lifecycle acceptance from its native probe or platform metadata.
- Independent review, exact candidate Gitea tests and source publication.
- Parent-coordinated signing-key provisioning and reviewed core public-key pin;
  never generate/store a signing key in a checkout or pass one on argv.
- Approved source SHA variable only after the above, immutable release verification
  and separately authorized catalog activation/install/update/rollback evidence.

No source push, signing, artifact publication or live installation is authorized
by merely adding this workflow. See [native-ui-v1.md](native-ui-v1.md),
[runtime-release-publishing-v1.md](runtime-release-publishing-v1.md) and
[service-package-v1.md](service-package-v1.md).
