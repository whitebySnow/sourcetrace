from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sourcetrace.core.logging import get_logger

logger = get_logger(__name__)


class FieldError(BaseModel):
    field: str
    message: str
    code: str


class ErrorResponse(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    errors: list[FieldError] | None = None


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _response(
    request: Request,
    error: AppError,
    *,
    field_errors: list[FieldError] | None = None,
) -> JSONResponse:
    error_slug = error.code.lower().replace("_", "-")
    body = ErrorResponse(
        type=f"/errors/{error_slug}",
        title=error.code.replace("_", " ").title(),
        status=error.status_code,
        detail=error.message,
        instance=request.url.path,
        code=error.code,
        request_id=_request_id(request),
        errors=field_errors,
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump())


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        logger.warning(
            "operational_error",
            code=error.code,
            request_id=_request_id(request),
        )
        return _response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        field_errors = [
            FieldError(
                field=".".join(str(part) for part in item["loc"] if part != "body"),
                message=item["msg"],
                code=item["type"],
            )
            for item in error.errors()
        ]
        return _response(
            request,
            AppError(
                code="VALIDATION_ERROR",
                message="Request validation failed",
                status_code=422,
            ),
            field_errors=field_errors,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception(
            "unexpected_error",
            request_id=_request_id(request),
            error_type=type(error).__name__,
        )
        return _response(
            request,
            AppError(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred",
                status_code=500,
            ),
        )
