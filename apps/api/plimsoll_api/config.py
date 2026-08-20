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

    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    environment: str = "development"
    log_level: str = "INFO"
    version: str = "0.1.0"
    git_sha: str = "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
