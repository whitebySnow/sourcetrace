from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sourcetrace.core.config import get_settings
from sourcetrace.db.base import Base
from sourcetrace.db.session import get_session
from sourcetrace.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    schema = f"test_{uuid4().hex}"
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client

    async with engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await engine.dispose()
