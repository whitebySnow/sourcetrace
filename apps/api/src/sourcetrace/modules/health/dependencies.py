from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
from sourcetrace.modules.health.repository import (
    DatabaseReadinessProbe,
    RedisReadinessProbe,
)
from sourcetrace.modules.health.service import ReadinessService


async def get_redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)
    try:
        yield client
    finally:
        await client.aclose()


def get_readiness_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> ReadinessService:
    return ReadinessService(
        DatabaseReadinessProbe(session),
        RedisReadinessProbe(redis),
    )
