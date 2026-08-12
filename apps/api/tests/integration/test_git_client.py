"""The fixture is a real Git server, reachable from the API container."""

import subprocess

import pytest

pytestmark = pytest.mark.integration

COMPOSE = ["docker", "compose", "-f", "infrastructure/docker/docker-compose.yml"]


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
