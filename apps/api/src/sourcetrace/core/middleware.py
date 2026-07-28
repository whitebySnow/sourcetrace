from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sourcetrace.core.logging import get_logger

logger = get_logger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, request_id_header: str) -> None:
        self.app = app
        self.request_id_header = request_id_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(self.request_id_header) or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = perf_counter()
        status_code = 500

        async def send_with_context(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[self.request_id_header] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            logger.info(
                "request_completed",
                request_id=request_id,
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                elapsed_ms=round((perf_counter() - started_at) * 1000, 2),
            )
