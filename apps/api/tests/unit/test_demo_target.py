import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "images" / "demo-target"))

from app import ERROR_RATE, LATENCY_PROFILE_MS, app


def test_every_documented_endpoint_exists() -> None:
    client = TestClient(app)
    for path in ("/login", "/browse", "/cart", "/checkout"):
        assert client.get(path).status_code in (200, 500)


def test_healthz_never_fails() -> None:
    client = TestClient(app)
    for _ in range(20):
        assert client.get("/healthz").status_code == 200


def test_latency_profile_covers_every_endpoint_and_is_ordered() -> None:
    assert set(LATENCY_PROFILE_MS) == {"login", "browse", "cart", "checkout"}
    for low, high in LATENCY_PROFILE_MS.values():
        assert 0 < low < high


def test_error_rate_is_low_but_non_zero() -> None:
    assert 0 < ERROR_RATE < 0.05
