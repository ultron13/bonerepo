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


def test_artifacts_are_given_an_expiry() -> None:
    """The largest thing this system produces, and nothing deleted one.

    Half a gigabyte of JTL files had collected on a development machine
    running nothing but the test suite; a real hour-long test writes hundreds
    of megabytes per generator. The rule lives on the bucket rather than in a
    job, because a job would have to list every object to find the old ones --
    getting slower exactly as it became more necessary.
    """
    from botocore.exceptions import ClientError

    from plimsoll_api.config import get_settings

    storage.ensure_bucket()
    try:
        found = storage._client().get_bucket_lifecycle_configuration(
            Bucket=get_settings().s3_bucket
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # Only a store that cannot do this at all is a skip. "No configuration
        # exists" is the failure this test is for, and the first version
        # skipped on both -- so it reported success while the rule was being
        # rejected and the warning was going only to a log.
        if code in ("NotImplemented", "MethodNotAllowed"):
            pytest.skip(f"this object store has no lifecycle support: {exc}")
        raise AssertionError(f"no expiry rule is configured on the bucket: {code}") from exc

    rules = {rule["ID"]: rule for rule in found["Rules"]}
    assert "plimsoll-artifacts" in rules, sorted(rules)
    rule = rules["plimsoll-artifacts"]
    assert rule["Status"] == "Enabled"
    assert rule["Expiration"]["Days"] == get_settings().artifact_retention_days
    # A failed upload leaves parts nothing will complete, and they are billed
    # like storage because they are storage. Asserted only where the store
    # reports it back: MinIO accepts the clause and does not return it, and
    # demanding it here would be a test about MinIO rather than about this.
    abort = rule.get("AbortIncompleteMultipartUpload")
    if abort is not None:
        assert abort["DaysAfterInitiation"] == 7
