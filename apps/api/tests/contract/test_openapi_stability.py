from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from sourcetrace.main import create_app


def _routes_missing_validation_descriptions(
    routes: Iterable[BaseRoute],
) -> list[str]:
    routes_missing_descriptions: list[str] = []

    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        if 422 in route.responses:
            validation_response = route.responses[422]
        elif "422" in route.responses:
            validation_response = route.responses["422"]
        else:
            continue
        if "description" in validation_response:
            continue
        methods = ",".join(sorted(route.methods))
        routes_missing_descriptions.append(f"{methods} {route.path}")

    return routes_missing_descriptions


def test_explicit_validation_responses_define_stable_descriptions() -> None:
    assert _routes_missing_validation_descriptions(create_app().routes) == []


def test_stability_check_accepts_string_status_code_keys() -> None:
    app = FastAPI()

    @app.get("/synthetic", responses={"422": {}})
    async def synthetic_route() -> None:
        return None

    assert _routes_missing_validation_descriptions(app.routes) == ["GET /synthetic"]
