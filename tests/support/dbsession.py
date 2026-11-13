"""Throwaway in-memory SQLite session for unit tests that need real DB writes.

The `db` fixture is registered for every test by `pytest_plugins` in tests/conftest.py.
"""

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spoolman.database import models


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, autocommit=False, autoflush=True, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


async def make_user(db: AsyncSession, sub: str = "a", name: str = "A", admin: bool = False) -> models.User:  # noqa: FBT001, FBT002
    user = models.User(
        oidc_sub=sub,
        display_name=name,
        is_admin=admin,
        disabled=False,
        created=datetime.utcnow().replace(microsecond=0),
    )
    db.add(user)
    await db.flush()
    return user
