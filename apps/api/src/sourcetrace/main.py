from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sourcetrace.api.router import api_router
from sourcetrace.core.config import get_settings
from sourcetrace.core.errors import install_exception_handlers
from sourcetrace.core.logging import configure_logging, get_logger
from sourcetrace.core.middleware import RequestContextMiddleware
from sourcetrace.db.session import close_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("application_started", environment=settings.app_env)
    yield
    await close_database()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", settings.api_request_id_header],
    )
    app.add_middleware(
        RequestContextMiddleware,
        request_id_header=settings.api_request_id_header,
    )
    install_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
