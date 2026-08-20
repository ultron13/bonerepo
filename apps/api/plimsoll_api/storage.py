"""Object storage, and the presigned URLs that keep credentials out of
generators.

A generator runs a user-supplied plan. Handing it an object-store key would put
a durable credential inside that blast radius, so instead the control plane
signs exactly the key it is willing to accept, for exactly as long as it needs
to be accepted.
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from plimsoll_api.config import get_settings

# One path segment, no traversal, no separators. The agent chooses the name;
# this is what stops the name choosing the location.
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

BUNDLE_TTL_SECONDS = 3600
ARTIFACT_PUT_TTL_SECONDS = 3600
ARTIFACT_GET_TTL_SECONDS = 300


def _build(endpoint: str) -> Any:
    settings = get_settings()
    return boto3.client(
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
