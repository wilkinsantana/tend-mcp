# Isolated component worker and image build

The component worker is now implemented in `src/tend_mcp/component_worker.py`.
It is separate from the prototype PAT/HTTP client: the image copies only this
stdlib-only worker and the package version. There is no listener, core API
client, credential, Docker socket, filesystem/exec tool, or automatic operation.

## Private worker-v1 protocol

One JSON frame per newline, at most 64 KiB including input newline. Responses
are canonical compact ASCII JSON followed by newline. Exact common fields are
`protocol: 1`, `request_id` (1–64 ASCII letters/digits/underscore/hyphen), and
`operation`. Unknown fields, duplicates, non-finite values, invalid Unicode,
excessive nesting and malformed/oversized frames fail closed. Bad framing ends
the process without unbounded draining; errors never echo raw input.

- `hello`: component ID, package version, protocol and supported capability IDs.
- `tools.list`: descriptors for the three read-only projection tools.
- `render`: additionally requires `capability` and `result`. Result has exactly
  `items` and boolean `has_more`. At most 100 unique-ID items, with bounded
  strings and no control characters. Apps use `id/name/status`; deployments
  use `id/app_id/status`; servers use `id/name/health`. Unknown fields are
  refused, not copied into output. Results contain matching MCP text and
  structured content. Names remain untrusted data, not coding-agent instructions.

This is not external MCP ingress or an authorization API. Core must authenticate,
filter resources and construct every projection before dispatch. Tool metadata
and process identity grant no user operation. The fixed `--probe` invocation
emits a canonical hello response and exits; ordinary invocation serves stdio.

## Actual image build path

`Dockerfile.worker` uses a digest-pinned Python 3.12 base. `.dockerignore` excludes
Git metadata, credentials and all unrelated source. The worker runs as UID/GID
65532, with the fixed isolated Python entrypoint and no inherited command.

```sh
python scripts/check-worker-image.py --output dist/worker-image
```

This explicitly uses only the local Docker socket on a trusted disposable
Docker-enabled runner. It builds the worker, saves it, normalizes the image,
loads the exact normalized image ID, starts a private stdin worker container,
probes it through fixed exec, verifies isolation and packages its OCI layout.
It removes only its own label-bound container and unique tags; shared immutable
image/cache content may remain. No production environment, keys or volumes are
used. Local Docker permission failure is a pending integration gate, not a pass.

The normalizer accepts trusted Docker-save build output, never a caller's remote
URL or arbitrary runtime archive. It does not extract layer TARs onto the host.
It verifies layer/diff-ID identity, strips build history/inherited environment
metadata and writes a minimal OCI config plus tag-free Docker-load archive.
It requires new output paths; failure may leave partial output in the disposable
build workspace, which must be discarded. A normalizer failure cannot proceed
to signing or upload. This tool is not an adversarial archive sandbox.

Fixture errors identify a fixed stage and subprocess exit code without echoing
raw output, arguments, paths or metadata. The normalizer maps known rejection
reasons to stable exit codes in `ERROR_EXIT_CODES` (20–31); unknown errors use 2.
For example, 21 rejects the archive structure, 28 rejects unsafe runtime config,
and 31 rejects the layer/diff-ID match. These diagnostics do not relax checks or
prove an underlying Docker-format cause without the corresponding runner result.

The Gitea worker-image job is required before source mirroring. It currently
builds and verifies artifacts only: production signing, artifact publication and
catalog activation remain separate gates. No image has been verified merely
because the local Python tests pass.

## Core supervision status

Core now has an internal managed-UI/signed-artifact/daemon-image adoption seam
and generation-fenced Docker supervision. Internal installation now imports a
verified tag-free Docker-load stream, bounds and closes all response statuses,
and checks exact daemon identity before disabled adoption. It has no public
install/start route or startup registration. Existing catalog disable/remove
now invokes a shutdown-only bridge: durable fencing and verified retirement
precede UI mutation. Unknown outcomes preserve the package, removal requires
admin browser authority and the reviewed name, and a shutdown fence grants no
new start. Generic activation/update/rollback remains unavailable. Public
consent-bound installation, actual signed artifacts, authenticated ingress and
grants remain pending; this is not production installation support yet.

Core's opt-in `test_mcp_docker_integration.py` consumes the service ZIP from the
command above. It exercises actual core import/start/disable/lost-reply recovery
with an inert managed UI and ephemeral test trust. It independently checks
container absence before fallback cleanup, preserves foreign occupants and
attempts cleanup for every owned birth. Explicit local-Docker authorization is
required; a skipped or inaccessible fixture never counts as lifecycle acceptance.
