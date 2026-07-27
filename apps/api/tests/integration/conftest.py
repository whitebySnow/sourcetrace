from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sourcetrace.core.config import get_settings
from sourcetrace.db.base import Base
from sourcetrace.db.session import get_session
from sourcetrace.main import create_app
from sourcetrace.modules.documents.router import get_ingestion_queue


class RecordingIngestionQueue:
    def __init__(self) -> None:
        self.version_ids: list[UUID] = []

    async def enqueue(self, version_id: UUID) -> None:
        self.version_ids.append(version_id)


@pytest.fixture
def ingestion_queue() -> RecordingIngestionQueue:
    return RecordingIngestionQueue()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = f"test_{uuid4().hex}"
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(Base.metadata.create_all)

    yield session_factory

    async with engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as database_session:
        yield database_session


@pytest.fixture
async def client(
    session: AsyncSession,
    ingestion_queue: RecordingIngestionQueue,
) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_ingestion_queue] = lambda: ingestion_queue
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
