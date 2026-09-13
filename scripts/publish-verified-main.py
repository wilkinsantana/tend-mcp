"""Fast-forward GitHub main to the exact commit approved by the Gitea job graph."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile


def git(*args: str, env=None) -> str:
    return subprocess.check_output(['git', *args], env=env, text=True).strip()


def ancestor(older: str, newer: str) -> bool:
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('Could not establish publication ancestry')
    return result.returncode == 0


def publish(sha: str, remote: str, env=None) -> str:
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or git('rev-parse', 'HEAD') != sha:
        raise RuntimeError('Publication requires the exact verified checkout')
    if git('status', '--porcelain', '--untracked-files=no'):
        raise RuntimeError('Publication requires an unchanged checkout')
    for _ in range(3):
        git('-c', 'credential.helper=', 'fetch', '--no-tags', remote, 'refs/heads/main', env=env)
        current = git('rev-parse', 'FETCH_HEAD')
        if current == sha or ancestor(sha, current):
            return 'already published' if current == sha else 'superseded by a newer published commit'
        if not ancestor(current, sha):
            raise RuntimeError('GitHub main has diverged; refusing to overwrite it')
        result = subprocess.run(['git', '-c', 'credential.helper=', 'push', remote,
                                 sha + ':refs/heads/main'], env=env, capture_output=True)
        if result.returncode == 0:
            # Verify the remote after the push; a newer concurrent publication
            # is acceptable, but a missing or unrelated commit is not.
            git('-c', 'credential.helper=', 'fetch', '--no-tags', remote, 'refs/heads/main', env=env)
            if ancestor(sha, git('rev-parse', 'FETCH_HEAD')):
                return 'published'
            raise RuntimeError('Could not verify the published commit')
    raise RuntimeError('GitHub publication failed; check credentials or concurrent changes')


def main() -> None:
    if os.environ.get('GITEA_EVENT_NAME') != 'push' or os.environ.get('GITEA_REF') != 'refs/heads/main':
        raise RuntimeError('Only Gitea main push runs may publish')
    if not os.environ.get('GH_PUBLISH_TOKEN'):
        raise RuntimeError('Configure the GH_PUBLISH_TOKEN Gitea Actions secret')
    with tempfile.TemporaryDirectory() as directory:
        askpass = Path(directory) / 'askpass.py'
        askpass.write_text('#!/usr/bin/env python3\nimport os,sys\nprint("x-access-token" if "Username" in sys.argv[1] else os.environ["GH_PUBLISH_TOKEN"])\n')
        askpass.chmod(0o700)
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_TRACE')}
        env.update(GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT='0')
        sha = os.environ.get('VERIFIED_SHA', '')
        result = publish(sha, 'https://github.com/wilkinsantana/tend-mcp.git', env)
        print(f'{sha}: {result}')


if __name__ == '__main__':
    main()
