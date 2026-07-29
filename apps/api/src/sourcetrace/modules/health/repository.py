from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DatabaseReadinessProbe:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self) -> bool:
        await self._session.execute(text("SELECT 1"))
        return True


class RedisReadinessProbe:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def check(self) -> bool:
        return bool(await self._client.ping())
