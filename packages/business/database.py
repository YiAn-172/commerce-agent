from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def database_url() -> str:
    return os.getenv(
        "MYSQL_DSN",
        "mysql+asyncmy://commerce:change-me@mysql:3306/commerce",
    )


def create_engine(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        url or database_url(),
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session, session.begin():
        yield session
