from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sourcetrace.main import app
from sourcetrace.modules.health.dependencies import get_readiness_service
from sourcetrace.modules.health.service import ReadinessResult


class StubReadinessService:
    def __init__(self, result: ReadinessResult) -> None:
        self._result = result

    async def check(self) -> ReadinessResult:
        return self._result


def _override_readiness(result: ReadinessResult) -> None:
    app.dependency_overrides[get_readiness_service] = lambda: StubReadinessService(result)


@pytest.fixture(autouse=True)
def clear_readiness_override() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_readiness_service, None)


def test_health_returns_ok_and_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_ready_returns_ok_when_database_and_redis_are_available(client: TestClient) -> None:
    _override_readiness(ReadinessResult(database=True, redis=True))

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_ready_returns_503_when_a_required_dependency_is_unavailable(
    client: TestClient,
) -> None:
    _override_readiness(ReadinessResult(database=True, redis=False))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "unavailable"},
    }


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "request-123"})

    assert response.headers["X-Request-ID"] == "request-123"
