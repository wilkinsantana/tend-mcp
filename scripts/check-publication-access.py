"""Safe publication credential diagnostics: status codes only, no bodies/secrets."""
from __future__ import annotations

import os
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    token = os.environ.get("GH_PUBLISH_TOKEN", "")
    if not token:
        print("Publication credential: missing", flush=True)
        return
    if token != token.strip():
        print("Publication credential has surrounding whitespace; correct the saved value.", flush=True)
        return
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for label, path in (
        ("identity", "/user"),
        ("repository metadata", "/repos/wilkinsantana/tend-mcp"),
        ("main branch contents access", "/repos/wilkinsantana/tend-mcp/branches/main"),
    ):
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "User-Agent": "tend-mcp-publication-check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with opener.open(request, timeout=20) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        except (urllib.error.URLError, ValueError, TimeoutError):
            print(f"{label}: transport or credential-format failure", flush=True)
            continue
        print(f"{label}: HTTP {status}", flush=True)
    print("Read checks do not prove push permission; the verified-commit publisher remains authoritative.", flush=True)


if __name__ == "__main__":
    main()
