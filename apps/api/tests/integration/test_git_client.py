"""The fixture is a real Git server, and the client talks to it.

The client runs in this process against the fixture's published port; the
container tests below prove the API image can reach it by service name.
"""

import asyncio
import subprocess
from typing import Any

import pytest

from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref

pytestmark = pytest.mark.integration

COMPOSE = ["docker", "compose", "-f", "infrastructure/docker/docker-compose.yml"]

PUBLIC = "http://localhost:8081/public/plans.git"
PRIVATE = "http://localhost:8081/private/plans.git"
TOKEN = b"plimsoll:plimsoll-fixture-token"


def _in_api(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [*COMPOSE, "exec", "-T", "api", *command],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_api_container_has_git() -> None:
    result = _in_api("git", "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("git version")


def test_the_public_fixture_repository_advertises_its_branches() -> None:
    result = _in_api("git", "ls-remote", "http://script-fixture/public/plans.git")
    assert result.returncode == 0, result.stderr
    assert "refs/heads/main" in result.stdout
    assert "refs/heads/broken" in result.stdout


def test_the_private_fixture_repository_refuses_an_anonymous_caller() -> None:
    result = _in_api(
        "env",
        "GIT_TERMINAL_PROMPT=0",
        "git",
        "ls-remote",
        "http://script-fixture/private/plans.git",
    )
    assert result.returncode != 0


async def test_a_ref_resolves_to_a_commit() -> None:
    resolution = await resolve_ref(GitAccess(PUBLIC), "main")
    assert len(resolution.sha) == 40
    assert set(resolution.sha) <= set("0123456789abcdef")


async def test_a_commit_sha_resolves_to_itself() -> None:
    """A pinned commit is already resolved; ls-remote would not know it."""
    access = GitAccess(PUBLIC)
    tip = (await resolve_ref(access, "main")).sha
    assert (await resolve_ref(access, tip)).sha == tip


async def test_an_unknown_ref_is_an_error() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess(PUBLIC), "no-such-branch")


async def test_an_unreachable_host_is_an_error() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess("http://nowhere.invalid/r.git"), "main")


async def test_a_credential_opens_the_private_repository() -> None:
    resolution = await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", TOKEN), "main")
    assert len(resolution.sha) == 40


async def test_the_private_repository_refuses_a_wrong_credential() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", b"plimsoll:wrong"), "main")


async def test_the_private_repository_refuses_no_credential() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess(PRIVATE), "main")


async def test_the_plan_file_arrives_in_the_checkout() -> None:
    access = GitAccess(PUBLIC)
    resolution = await resolve_ref(access, "main")
    async with fetch_plan(access, resolution.sha, "perf/checkout.jmx") as root:
        assert (root / "perf" / "checkout.jmx").read_text().startswith("<?xml")
        assert (root / "perf" / "plimsoll.yaml").exists()
        assert (root / "perf" / "data" / "users.csv").exists()


async def test_the_checkout_is_removed_afterwards() -> None:
    access = GitAccess(PUBLIC)
    resolution = await resolve_ref(access, "main")
    async with fetch_plan(access, resolution.sha, "perf/checkout.jmx") as root:
        kept = root
    assert not kept.exists()


async def test_the_credential_is_not_visible_in_the_process_table() -> None:
    """A secret on argv is readable by any process on the host."""
    seen: list[str] = []
    original = asyncio.create_subprocess_exec

    async def recording(*command: str, **kwargs: Any) -> Any:
        seen.append(" ".join(command))
        return await original(*command, **kwargs)

    asyncio.create_subprocess_exec = recording  # type: ignore[assignment]
    try:
        await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", TOKEN), "main")
    finally:
        asyncio.create_subprocess_exec = original

    assert seen
    assert not any("plimsoll-fixture-token" in command for command in seen)
