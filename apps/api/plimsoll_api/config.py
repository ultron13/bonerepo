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

    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    environment: str = "development"
    log_level: str = "INFO"
    version: str = "0.1.0"
    git_sha: str = "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
