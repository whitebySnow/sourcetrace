import asyncio
from dataclasses import dataclass
from typing import Protocol


class ReadinessProbe(Protocol):
    async def check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    database: bool
    redis: bool

    @property
    def ready(self) -> bool:
        return self.database and self.redis


class ReadinessService:
    def __init__(self, database: ReadinessProbe, redis: ReadinessProbe) -> None:
        self._database = database
        self._redis = redis

    async def check(self) -> ReadinessResult:
        database, redis = await asyncio.gather(
            self._database.check(),
            self._redis.check(),
            return_exceptions=True,
        )
        return ReadinessResult(
            database=database is True,
            redis=redis is True,
        )
