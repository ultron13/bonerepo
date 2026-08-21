"""Object storage, and the presigned URLs that keep credentials out of
generators.

A generator runs a user-supplied plan. Handing it an object-store key would put
a durable credential inside that blast radius, so instead the control plane
signs exactly the key it is willing to accept, for exactly as long as it needs
to be accepted.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from plimsoll_api.config import get_settings

logger = logging.getLogger(__name__)

# One path segment, no traversal, no separators. The agent chooses the name;
# this is what stops the name choosing the location.
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

BUNDLE_TTL_SECONDS = 3600
ARTIFACT_PUT_TTL_SECONDS = 3600
ARTIFACT_GET_TTL_SECONDS = 300


def _add_content_md5(request: Any, **_: Any) -> None:
    """Some S3 implementations still require Content-MD5 on this request.

    botocore moved to CRC32 checksums and stopped sending MD5 except where it
    is mandatory; MinIO's PutBucketLifecycleConfiguration still asks for it and
    refuses the request without one. Harmless where it is not required, and
    the alternative was an expiry rule that is quietly never applied -- which
    is what happened, with the refusal going only to a log line.
    """
    body = request.body or b""
    if isinstance(body, str):
        body = body.encode()
    request.headers["Content-MD5"] = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324


def _build(endpoint: str) -> Any:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        # Path addressing: MinIO does not serve virtual-host buckets by default,
        # and a signature computed for the wrong style fails at use rather than
        # at signing, which is a miserable thing to debug.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.meta.events.register("before-sign.s3.PutBucketLifecycleConfiguration", _add_content_md5)
    return client


@lru_cache
def _client() -> Any:
    """For the control plane's own transfers, and for URLs used inside the
    cluster -- an agent reaches the store by its service name."""
    return _build(get_settings().s3_endpoint)


@lru_cache
def _public_client() -> Any:
    """For URLs that leave the cluster.

    SigV4 signs the Host header, so a URL cannot be rewritten after signing --
    it has to be signed for the host that will be used. A deployment where one
    name serves both leaves `s3_public_endpoint` empty and gets one client.
    """
    settings = get_settings()
    return _build(settings.s3_public_endpoint or settings.s3_endpoint)


def bundle_key(run_id: uuid.UUID) -> str:
    return f"runs/{run_id}/bundle.tar.gz"


def artifact_key(run_id: uuid.UUID, ordinal: int, name: str) -> str:
    if not SAFE_NAME.match(name):
        raise ValueError(f"{name!r} is not a valid artifact name.")
    return f"runs/{run_id}/generators/{ordinal}/{name}"


def ensure_bucket() -> None:
    bucket = get_settings().s3_bucket
    try:
        _client().head_bucket(Bucket=bucket)
    except ClientError:
        # Racing another process to create it is fine; the loser sees it exists.
        try:
            _client().create_bucket(Bucket=bucket)
        except ClientError:
            pass
    _ensure_lifecycle(bucket)


def _ensure_lifecycle(bucket: str) -> None:
    """Artifacts expire, or they accumulate for ever.

    This is the largest thing this system produces: a raw JTL from an
    hour-long test is hundreds of megabytes per generator, and nothing here
    ever deleted one. Half a gigabyte had collected on a development machine
    running nothing but the test suite.

    The rule is set on the bucket rather than enforced by a job, because the
    object store already knows how to do this and a job that has to list every
    object to find the old ones is a job that gets slower exactly as it
    becomes more necessary.

    A store that does not support lifecycle rules is not an error: expiry is
    then the operator's to arrange, and refusing to start over it would be
    worse than saying so.
    """
    days = get_settings().artifact_retention_days
    if days <= 0:
        # Deliberately kept for ever. Some deployments have to.
        return
    try:
        _client().put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "plimsoll-artifacts",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": days},
                        # An upload that failed part way leaves parts nothing
                        # will ever complete, and they are billed like storage
                        # because they are storage.
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            },
        )
    except ClientError as exc:
        logger.warning("could not set an expiry rule on %s: %s", bucket, exc)


def presign_put(key: str, *, seconds: int = ARTIFACT_PUT_TTL_SECONDS) -> str:
    url: str = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=seconds,
    )
    return url


def presign_get(key: str, *, seconds: int = ARTIFACT_GET_TTL_SECONDS) -> str:
    url: str = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=seconds,
    )
    return url


def presign_get_public(key: str, *, seconds: int = ARTIFACT_GET_TTL_SECONDS) -> str:
    """A download link for someone outside the cluster."""
    url: str = _public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=seconds,
    )
    return url


def put_bytes(key: str, payload: bytes) -> None:
    _client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=payload)


def get_bytes(key: str) -> bytes:
    body: bytes = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()
    return body


def list_prefix(prefix: str) -> list[dict[str, Any]]:
    response = _client().list_objects_v2(Bucket=get_settings().s3_bucket, Prefix=prefix)
    return [
        {
            "key": item["Key"],
            "name": item["Key"].rsplit("/", 1)[-1],
            "size": item["Size"],
            "lastModified": item["LastModified"],
        }
        for item in response.get("Contents", [])
    ]
