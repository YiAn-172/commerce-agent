from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.business.models import ApprovalTask, Base, ServiceTicket, User

NOW = datetime(2026, 9, 18, 3, 0, 0)


@pytest.fixture
async def approval_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(
                id="usr_approval_0001",
                external_ref="approval-subject-1",
                display_name="审批演示用户",
                masked_phone="138****0001",
                region="华东",
                created_at=NOW,
            )
        )
        session.add(
            ServiceTicket(
                id="ticket_approval_0001",
                principal_id="usr_approval_0001",
                order_id="ord_approval_0001",
                item_id="item_approval_0001",
                request_type="return",
                reason_code="DO_NOT_WANT",
                description_redacted="不想要了",
                idempotency_key="idem_approval_000001",
                idempotency_fingerprint="a" * 64,
                risk_level="high",
                approval_status="pending_human_approval",
                refund_amount_snapshot=Decimal("399.00"),
                state_version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            ApprovalTask(
                id="approval_test_000001",
                ticket_id="ticket_approval_0001",
                action_type="mock_refund_review",
                status="pending_human_approval",
                amount_snapshot=Decimal("399.00"),
                state_version=1,
                session_id=None,
                checkpoint_thread_id=None,
                decided_by=None,
                decision_reason=None,
                expires_at=NOW + timedelta(hours=24),
                resumed_at=None,
                resume_result=None,
                created_at=NOW,
                decided_at=None,
                updated_at=NOW,
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()
