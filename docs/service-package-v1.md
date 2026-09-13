# service-oci-zip-v1 publisher

`pack-runtime-service.py` builds the exact service transport container accepted
by core's `mcp_service_package.py`. Run only in a trusted publisher workspace:

```sh
uv run python scripts/pack-runtime-service.py \
  --layout build/normalized-oci-layout --output build/mcp-service.zip
```

This packages a pre-existing **normalized** OCI layout. It does not build an
image, import companion source, run Docker, download layers, read signing keys,
or activate a component. It does not validate the OCI semantic graph or prove
that the service is runnable. Core independently performs those graph/config
checks; actual image import/confinement/lifecycle tests remain pending.

## Layout and output

The input directory contains only `oci-layout`, `index.json`, and
`blobs/sha256/<lowercase SHA-256>` files. Files must be nonempty regular files;
root/child directory and file symlinks, special files, unrelated entries,
incorrect blob hashes and detectable changes during reading are refused.
Parent directories are trusted workspace configuration, not a filesystem
sandbox against other jobs or host administrators. Use isolated job workspaces.

The packager holds directory/file descriptors during processing. It admits at
most 128 entries, 32 KiB per layout/index file, and 512 MiB including the exact
ZIP overhead. Names are sorted and timestamps fixed to 1980-01-01. ZIP entries
are stored without compression, comments or extra fields and declare Unix
regular mode 0600. Layer bytes remain opaque and are never extracted.

Output is prepared in a new private sibling file, fsynced, atomically linked
without overwrite, then directory-fsynced. An existing destination file or
symlink is preserved. The output parent cannot be an input-layout directory.
Temporary siblings are removed on success and failure under normal filesystem
operation. A failed command must block release upload. In particular, a failure
after the final link may leave a complete output: `publication_uncertain` must
not be treated as success or worked around by deleting an unrelated file.
Inspect/reconcile the retained output explicitly. No hard timeout is claimed
for a stuck kernel filesystem operation.

Success prints only format, SHA-256 and byte count. Arguments and filesystem
errors are redacted. Sign the resulting immutable ZIP using
[`runtime-release-publishing-v1.md`](runtime-release-publishing-v1.md); keep it
unchanged through upload and require independent core verification.

## Required normalized OCI profile

The panel's `docs/strategy/tend-mcp-service-package-v1.md` is the complete
contract. In summary: one schema-2 OCI image, exact matching Linux platform,
1–64 distinct uncompressed TAR layers, complete digest/size closure with no
orphan blobs/external URLs, and a minimal config containing only architecture,
os, rootfs and the fixed runtime config. Runtime values are:

- User `65532:65532`, working directory `/app`, empty `Cmd`.
- Entrypoint `/usr/local/bin/python -I -u -m tend_mcp.component_worker`.
- Environment `PATH=/usr/local/bin:/usr/bin:/bin`, `LANG=C.UTF-8`, in that order.

No hooks, volumes, exposed ports, shell overrides or exporter-specific metadata
are accepted. A normal OCI exporter may need a separate reviewed normalization
step; this tool does not silently rewrite signed image identities. The actual
component worker and an image build/normalization/probe path are now implemented;
see [worker-runtime-v1.md](worker-runtime-v1.md). Docker acceptance and production
artifact publication remain separate required gates.

## Verification

```sh
uv run pytest -q tests/test_service_packager.py tests/test_release_publisher.py
uv run ruff check scripts/pack-runtime-service.py tests/test_service_packager.py
uv run ruff format --check scripts/pack-runtime-service.py tests/test_service_packager.py
uv run mypy scripts/pack-runtime-service.py
```

44 publisher tests pass, including deterministic bytes, no-overwrite behavior,
symlink/FIFO/path refusal, mutation, byte budgets and durability-failure handling.
Gitea includes the packager in its lint/type gate but does not package or sign
production artifacts automatically.

The panel's opt-in `test_mcp_publisher_interop.py` invokes both independent
publisher CLIs over disposable, inert fixtures, then exercises core signature
verification, durable metadata acceptance, private staging and OCI validation.
Set `TEND_MCP_PUBLISHER_SCRIPTS` to this checkout's scripts directory when
running that test. No companion source is imported, no private test key is
retained, and no configured deployment is contacted. This establishes wire and
packaging interoperability, not a working component or safe Docker lifecycle.
