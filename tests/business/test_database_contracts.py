from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.business.models import Base, Order, ServiceTicket, User
from packages.business.repositories import BusinessRepository
from packages.contracts.order import OrderStatus

NOW = datetime(2026, 9, 15, 4, 0, 0)


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id="usr_demo_0001",
                    external_ref="subject-1",
                    display_name="演示用户001",
                    masked_phone="138****0001",
                    region="华东",
                    created_at=NOW,
                ),
                User(
                    id="usr_demo_0002",
                    external_ref="subject-2",
                    display_name="演示用户002",
                    masked_phone="139****0002",
                    region="华南",
                    created_at=NOW,
                ),
                Order(
                    id="ord_demo_000001",
                    user_id="usr_demo_0001",
                    status=OrderStatus.DELIVERED.value,
                    total_amount=Decimal("399.00"),
                    currency="CNY",
                    state_version=1,
                    created_at=NOW,
                    paid_at=NOW,
                    shipped_at=NOW,
                    delivered_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        await session.commit()
    yield session_factory
    await engine.dispose()


async def test_repository_enforces_order_ownership(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        repository = BusinessRepository(session)
        owned = await repository.get_owned_order("usr_demo_0001", "ord_demo_000001")
        denied = await repository.get_owned_order("usr_demo_0002", "ord_demo_000001")
    assert owned is not None
    assert denied is None


async def test_ticket_idempotency_key_is_unique_per_principal(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    common = {
        "principal_id": "usr_demo_0001",
        "order_id": "ord_demo_000001",
        "item_id": "item_demo_placeholder",
        "request_type": "return",
        "reason_code": "DEMO",
        "description_redacted": "演示",
        "idempotency_key": "idem_demo_12345678",
        "risk_level": "high",
        "approval_status": "pending_human_approval",
        "refund_amount_snapshot": Decimal("399.00"),
        "state_version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    async with factory() as session:
        session.add(
            ServiceTicket(
                id="ticket_demo_000001",
                idempotency_fingerprint="a" * 64,
                **common,
            )
        )
        await session.commit()
        session.add(
            ServiceTicket(
                id="ticket_demo_000002",
                idempotency_fingerprint="b" * 64,
                **common,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


def test_schema_contains_all_required_p3_tables() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "products",
        "skus",
        "inventory",
        "orders",
        "order_items",
        "logistics_events",
        "after_sales_rules",
        "service_tickets",
        "approval_tasks",
        "chat_sessions",
        "chat_messages",
        "agent_runs",
        "tool_call_logs",
        "knowledge_documents",
        "knowledge_versions",
        "knowledge_reindex_jobs",
        "evaluation_runs",
        "evaluation_results",
    }
