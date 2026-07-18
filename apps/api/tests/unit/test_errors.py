from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from sourcetrace.core.errors import install_exception_handlers
from sourcetrace.core.middleware import RequestContextMiddleware


def test_validation_errors_use_problem_details_envelope() -> None:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, request_id_header="X-Request-ID")
    router = APIRouter()

    @router.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    app.include_router(router)
    install_exception_handlers(app)

    with TestClient(app) as client:
        response = client.get("/items/not-an-integer")

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "/errors/validation-error"
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert body["instance"] == "/items/not-an-integer"
    assert body["request_id"]
    assert body["errors"][0]["field"] == "path.item_id"
