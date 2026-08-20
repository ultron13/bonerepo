"""Staging the plan once, centrally, instead of cloning per generator."""

import hashlib
import io
import tarfile
import uuid

import httpx
import pytest

from plimsoll_api import storage
from plimsoll_api.git.client import GitAccess, GitError, resolve_ref
from plimsoll_worker.bundle import stage

pytestmark = pytest.mark.integration

PUBLIC = "http://localhost:8081/public/plans.git"


async def _sha_of_main() -> str:
    return (await resolve_ref(GitAccess(PUBLIC), "main")).sha


async def test_the_bundle_carries_the_plan_and_everything_beside_it() -> None:
    storage.ensure_bucket()
    run_id = uuid.uuid4()
    reference = await stage(
        run_id,
        [
            {
                "access": GitAccess(PUBLIC),
                "commitSha": await _sha_of_main(),
                "planPath": "perf/checkout.jmx",
            }
        ],
    )

    async with httpx.AsyncClient() as client:
        payload = (await client.get(storage.presign_get(reference.key, seconds=60))).content
    assert hashlib.sha256(payload).hexdigest() == reference.sha256

    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        names = archive.getnames()
    assert "perf/checkout.jmx" in names
    assert "perf/plimsoll.yaml" in names
    assert "perf/data/users.csv" in names


async def test_staging_the_same_commit_twice_gives_the_same_digest() -> None:
    """Byte-identical inputs are a checksum, not an assumption."""
    storage.ensure_bucket()
    sha = await _sha_of_main()
    plans = [{"access": GitAccess(PUBLIC), "commitSha": sha, "planPath": "perf/checkout.jmx"}]

    first = await stage(uuid.uuid4(), plans)
    second = await stage(uuid.uuid4(), plans)
    assert first.sha256 == second.sha256


async def test_an_unreachable_repository_raises() -> None:
    with pytest.raises(GitError):
        await stage(
            uuid.uuid4(),
            [
                {
                    "access": GitAccess("http://nowhere.invalid/r.git"),
                    "commitSha": "0" * 40,
                    "planPath": "perf/checkout.jmx",
                }
            ],
        )
