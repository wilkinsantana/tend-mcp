# Native MCP management UI v1

Independent component `host.tend.mcp`, version `0.1.0`, native schema 2. The
entry point is default `activate(host) -> { mount(container), unmount() }`, as
established by core's ExtensionMount HostApi and calculator fixture. It calls no
invented host methods. There is no native host locale field in that interface:
initial locale follows document language, with a memory-only EN/ES selector.
The manifest declares `network` because core's native scanner requires it for
`fetch`, even for same-origin calls; installation must show that permission in
the normal core consent pipeline. This declaration does not grant external MCP
or panel authority. There are no storage permissions, imports, remote scripts
or runtime modules.

The versioned core contract is `docs/strategy/tend-mcp-public-read-v1.md`.
Only same-origin `/api/mcp/status`, `/api/mcp/apps`, `/api/mcp/clients`,
`/api/mcp/clients/{encoded_client_id}/revoke`, and `/api/auth/me` are used.
CSRF comes from `csrf_token`, sent as `X-CSRF-Token` for POSTs. Requests refuse
redirects and use no-store; core still owns all authorization and response bounds.
No endpoint supplied by an API is used as a fetch target or generated link.

Enrollment requires an explicit app selection, a nonempty name (max 64, matching
core; also checked before programmatic submissions), integer
expiry 1–30 days (default seven), consent, and recent administrator sign-in.
Core decides readiness and actual current grants. Only app summary/deployment
status reads are described; sibling apps, server-wide health, writes and payments
are denied. External assistants use separate MCP bearer credentials, not browser
cookies/PATs. Inventory uses only named fields, never renders credentials or
untrusted HTML, and revocation requires exact typed name confirmation.

Credentials are displayed once in a read-only textarea and copied only from an
explicit button. Dismissal, Escape and unmount clear the credential string and
input value. Late enrollment responses after unmount cannot recreate a dialog.
Requests abort on unmount. Clipboard contents are outside the component's memory
and cannot be recalled; a notice tells the administrator to clear them after use.
Lost or uncertain enrollment requires inventory refresh and named revocation
before re-enrollment; automatic retry is intentionally absent.

Native `<dialog>.showModal()` provides focus trapping and inert background;
Escape is blocked during writes, never by clicking the backdrop. Explicit cancel
and dismiss controls remain available afterward. Form labels, live fixed errors,
keyboard focus outlines and English/Spanish guidance are included. DOM tests mock
only the native dialog primitive. `scripts/native-ui-browser.mjs` independently
packages the ZIP, verifies module integrity, and mounts those bytes in Chromium
through the native mount/unmount contract. EN/ES at mobile/desktop widths covers
explicit selection, native modal behavior, credential clearing and explicit copy,
named revocation, uncertain responses without automatic retries and unavailable
state. APIs are intercepted: this is not the complete core HostApi/browser shell,
actual assistant enrollment or live worker evidence.

The exact-core contract fixture additionally runs the actual UI archive through
normal managed installation, scanner and HTTP SDK tests using daemon doubles.
Keep that result separate from the real service-package direct Docker/pinned SSH
fixture, and both separate from production signing and deployment.

## Deterministic transport

```sh
python scripts/pack-native-ui.py --source ui --output dist/tend-mcp-ui.zip
npm ci --ignore-scripts --no-audit --no-fund
npm run check && npm test
npx playwright install --with-deps chromium
npm run test:browser
uv run pytest -q tests/test_ui_packager.py
```

The browser check supports `PLAYWRIGHT_MODULE` and `PYTHON_BIN` overrides for
already-installed local tools; Gitea uses the locked development dependency and
its disposable runner. It binds loopback on an ephemeral port, refuses unexpected
requests and removes generated artifacts on exit. No live endpoint is used.

Create the output parent first. The packager reads only bounded regular UTF-8
`index.js` (no leaf symlinks), generates `extension.json`, and binds the module
with canonical base64 SHA-256 integrity. ZIP members are lexically sorted, stored,
Unix regular 0600, fixed 1980 timestamps, no extras/comments. Runtime/profile and
permission declarations follow existing core fixtures. Outputs are linked
atomically without overwrite and fsynced; an uncertain durability failure can
leave a complete output and requires reconciliation. Parent directories are a
trusted immutable CI workspace, not an adversarial filesystem sandbox.

Only the two runtime members enter the ZIP; tests and npm dependencies do not.
The stdlib worker-v1 translator, service ZIP and runtime envelope schemas are
unchanged. See [component-artifacts-v1.md](component-artifacts-v1.md) for the
separate, currently inactive production release lane.
