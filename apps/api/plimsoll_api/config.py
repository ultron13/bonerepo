from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLIMSOLL_", extra="ignore")

    database_url: str
    # Migrations and the seed connect as the owner; the runtime never does.
    migration_database_url: str
    redis_url: str
    s3_endpoint: str
    jwt_secret: str
    # Encrypts the credentials table. No default: a built-in key is the same as
    # no encryption, discovered later.
    credential_key: str
    # Keys that no longer encrypt anything but still open rows written before
    # the last rotation. Comma-separated. A rotation adds the outgoing key
    # here, re-encrypts, and then removes it -- without this step the rotation
    # is a data loss rather than a rotation.
    credential_keys_retired: str = ""

    # Where this deployment is reachable from outside. Single sign-on needs
    # both: the redirect URI is registered at the identity provider and must
    # match to the character, and the browser has to be sent somewhere real
    # afterwards. Deriving them from the request would let a forwarded host
    # header choose where a sign-in lands.
    public_api_url: str = "http://localhost:8000"
    public_web_url: str = "http://localhost:3000"

    # The issuer is the trust anchor for single sign-on: over plain HTTP
    # anything on the path can rewrite the discovery document, and with it
    # every endpoint and the key set tokens are checked against. Off by
    # default, so a deployment that wants it has to say so. The development
    # compose says so, because its provider fixture is a container on a
    # private network with no certificate.
    oidc_allow_insecure_issuer: bool = False

    # A webhook URL is supplied by a tenant and fetched by the control plane,
    # so by default it may only reach a public address -- otherwise it is a
    # way to read this deployment's own network, including the metadata
    # endpoint that hands out cloud credentials.
    #
    # Off by default and genuinely needed by some deployments: an on-premises
    # SIEM is often on the same private network. Turning it on means every
    # organisation on this instance can point a webhook at anything the
    # control plane can reach, so it belongs on a single-tenant install and
    # not on a shared one.
    webhook_allow_private_addresses: bool = False

    # How long raw run artifacts are kept in object storage. This is the
    # largest thing this system produces -- a JTL from an hour-long test is
    # hundreds of megabytes per generator -- and it is set as a lifecycle rule
    # on the bucket rather than enforced by a job that would have to list
    # every object to find the old ones. Zero keeps them for ever, which some
    # deployments have to.
    artifact_retention_days: int = 90

    # Creates organisations, and nothing else. An operator's credential rather
    # than one this product issues: bringing a tenant into being cannot be
    # authorised from inside a tenant, because there is nobody in it yet.
    #
    # Empty means the route refuses outright. A default here would be a way
    # into every deployment that never set one, and the blast radius even when
    # set is bounded -- it can create an empty organisation and its first
    # administrator, and it cannot read anybody's data, because row-level
    # security does not care what created the row.
    instance_token: str = ""

    # Defaults exist so a unit test can build the application without them.
    # The compose values are the same because this is the development MinIO; a
    # deployment sets all of them.
    s3_access_key: str = "plimsoll"
    # Development MinIO's password, the same value compose sets. Unlike
    # credential_key, a deployment that forgets to override this fails at
    # connect rather than quietly running without protection.
    s3_secret_key: str = "plimsoll_dev_secret"  # noqa: S105 - development default
    s3_bucket: str = "plimsoll-artifacts"
    s3_region: str = "us-east-1"
    # Where a presigned URL is signed for when it will be used from outside the
    # compose network -- a browser, or a test on the host. Empty means the
    # internal endpoint serves both, which is true of a real deployment.
    s3_public_endpoint: str = ""

    # Origins the browser interface is served from. An explicit list, never a
    # wildcard: this API is reached with a bearer token, and a permissive
    # origin would let any page a user visits spend it.
    cors_origins: str = "http://localhost:3000"

    # Failed sign-ins only. The per-address limit does the real work; the
    # per-account one is generous because a stranger must not be able to aim it
    # at a colleague.
    auth_failure_limit: int = 8
    auth_failure_limit_per_address: int = 40
    auth_failure_window_seconds: int = 900
    # Whether X-Forwarded-For may be believed. Off unless a deployment says it
    # sits behind a proxy that sets it: trusting it by default lets anyone claim
    # a fresh address per attempt and walk through the throttle.
    trust_proxy_headers: bool = False

    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    environment: str = "development"
    log_level: str = "INFO"
    version: str = "0.1.0"
    git_sha: str = "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
