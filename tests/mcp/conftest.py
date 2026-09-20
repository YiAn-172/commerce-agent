from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from packages.business.database import create_engine, session_factory


@pytest.fixture
async def mysql_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine()
    yield engine
    await engine.dispose()


@pytest.fixture
def mysql_factory(mysql_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return session_factory(mysql_engine)
