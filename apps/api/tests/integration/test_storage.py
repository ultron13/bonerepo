"""Object storage, against the MinIO the stack already runs."""

import time
import uuid

import httpx
import pytest

from plimsoll_api import storage

pytestmark = pytest.mark.integration


def test_the_bucket_is_created_idempotently() -> None:
    storage.ensure_bucket()
    storage.ensure_bucket()


def test_a_presigned_put_accepts_exactly_that_key() -> None:
    storage.ensure_bucket()
    run_id = uuid.uuid4()
    key = storage.artifact_key(run_id, 0, "results.jtl")

    url = storage.presign_put(key, seconds=60)
    response = httpx.put(url, content=b"sample,data\n")
    assert response.status_code in (200, 204), response.text

    assert [entry["name"] for entry in storage.list_prefix(f"runs/{run_id}/")] == ["results.jtl"]


def test_a_presigned_get_returns_what_was_put() -> None:
    storage.ensure_bucket()
    key = storage.artifact_key(uuid.uuid4(), 0, "jmeter.log")
    storage.put_bytes(key, b"hello")

    body = httpx.get(storage.presign_get(key, seconds=60)).content
    assert body == b"hello"


def test_an_expired_url_is_refused() -> None:
    """The signature is the whole of the authorisation, so its lifetime is the
    whole of the exposure."""
    storage.ensure_bucket()
    key = storage.artifact_key(uuid.uuid4(), 0, "results.jtl")
    storage.put_bytes(key, b"x")
    url = storage.presign_get(key, seconds=1)
    time.sleep(2)
    assert httpx.get(url).status_code in (403, 400)


def test_keys_are_scoped_to_their_run_and_ordinal() -> None:
    run_id = uuid.uuid4()
    assert storage.artifact_key(run_id, 3, "results.jtl") == (
        f"runs/{run_id}/generators/3/results.jtl"
    )
    assert storage.bundle_key(run_id) == f"runs/{run_id}/bundle.tar.gz"


def test_a_name_cannot_escape_its_prefix() -> None:
    """The agent names the artifact; the control plane decides where it lands."""
    with pytest.raises(ValueError):
        storage.artifact_key(uuid.uuid4(), 0, "../../etc/passwd")
