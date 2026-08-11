import pytest

from plimsoll_api.config import Settings


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLIMSOLL_DATABASE_URL", "postgresql+asyncpg://u@h/db")
    monkeypatch.setenv("PLIMSOLL_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("PLIMSOLL_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("PLIMSOLL_JWT_SECRET", "secret-value")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u@h/db"
    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 1209600


def test_missing_required_setting_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "REDIS_URL", "S3_ENDPOINT", "JWT_SECRET"):
        monkeypatch.delenv(f"PLIMSOLL_{key}", raising=False)
    with pytest.raises(ValueError):
        Settings()
