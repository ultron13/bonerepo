"""Talking to Git, with the secret kept off the command line.

`ps` on this container shows a git invocation with no credential in it: a token
reaches git through an askpass helper and the environment, an SSH key through a
0600 file named by GIT_SSH_COMMAND. Both live inside the per-operation
temporary directory, which is removed in a finally.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TIMEOUT_SECONDS = 60
# The username a token-only credential is presented under; the convention most
# Git hosts accept. Not a secret.
DEFAULT_TOKEN_USERNAME = "x-access-token"  # noqa: S105

ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$PLIMSOLL_GIT_USERNAME" ;;
  *) printf '%s' "$PLIMSOLL_GIT_PASSWORD" ;;
esac
"""


class GitError(Exception):
    """Git was unreachable, refused the credential, or did not know the ref."""


@dataclass(frozen=True)
class GitAccess:
    url: str
    kind: str | None = None
    secret: bytes | None = None


@dataclass(frozen=True)
class RefResolution:
    sha: str
    message: str | None = None
    committed_at: datetime | None = None


@asynccontextmanager
async def _workspace(access: GitAccess) -> AsyncIterator[tuple[dict[str, str], Path]]:
    workspace = Path(tempfile.mkdtemp(prefix="plimsoll-git-"))
    try:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workspace),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        if access.kind == "GIT_TOKEN" and access.secret is not None:
            secret = access.secret.decode()
            username, separator, password = secret.partition(":")
            if not separator:
                username, password = DEFAULT_TOKEN_USERNAME, secret
            askpass = workspace / "askpass.sh"
            askpass.write_text(ASKPASS)
            askpass.chmod(0o700)
            environment |= {
                "GIT_ASKPASS": str(askpass),
                "PLIMSOLL_GIT_USERNAME": username,
                "PLIMSOLL_GIT_PASSWORD": password,
            }
        elif access.kind == "GIT_SSH_KEY" and access.secret is not None:
            key = workspace / "id_key"
            material = access.secret
            key.write_bytes(material if material.endswith(b"\n") else material + b"\n")
            key.chmod(0o600)
            # Host keys are accepted on first use in v0.1: pinning needs a
            # known_hosts field the schema does not have, and HTTPS tokens are
            # the documented path.
            environment["GIT_SSH_COMMAND"] = (
                f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
                "-o StrictHostKeyChecking=accept-new"
            )
        yield environment, workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def _run(command: list[str], *, environment: dict[str, str], cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        raise GitError(f"Git did not respond within {TIMEOUT_SECONDS} seconds.") from exc

    if process.returncode != 0:
        # stderr can echo the URL but never the credential: it is not on argv.
        raise GitError(stderr.decode(errors="replace").strip() or "Git failed.")
    return stdout.decode(errors="replace")


async def resolve_ref(access: GitAccess, ref: str) -> RefResolution:
    async with _workspace(access) as (environment, _):
        output = await _run(
            ["git", "ls-remote", "--exit-code", access.url, ref], environment=environment
        )
    first = output.split("\n", 1)[0].strip()
    sha = first.split("\t", 1)[0] if first else ""
    if len(sha) != 40:
        raise GitError(f"Ref {ref!r} did not resolve to a commit.")
    return RefResolution(sha=sha)


@asynccontextmanager
async def fetch_plan(access: GitAccess, sha: str, plan_path: str) -> AsyncIterator[Path]:
    """Blobless and sparse: history and unrelated blobs never cross the wire.

    The plan's own directory is checked out, which brings the manifest and the
    data files beside it -- everything a plan references resolves by relative
    path from the plan file.
    """
    directory = Path(plan_path).parent.as_posix()
    async with _workspace(access) as (environment, workspace):
        checkout = workspace / "checkout"
        await _run(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--no-checkout",
                "--sparse",
                access.url,
                str(checkout),
            ],
            environment=environment,
        )
        await _run(
            ["git", "sparse-checkout", "set", "--no-cone", directory or "/*"],
            environment=environment,
            cwd=checkout,
        )
        await _run(["git", "checkout", "--quiet", sha], environment=environment, cwd=checkout)
        yield checkout
