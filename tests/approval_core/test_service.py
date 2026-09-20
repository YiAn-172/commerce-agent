from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.approval_core.contracts import ApprovalDecision
from packages.approval_core.service import ApprovalService, ApprovalStateConflict
from packages.business.models import ServiceTicket
from tests.approval_core.conftest import NOW


@pytest.mark.asyncio
async def test_decision_is_atomic_and_duplicate_click_is_idempotent(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = ApprovalService(approval_factory)
    first = await service.decide(
        "approval_test_000001",
        decision=ApprovalDecision.APPROVED,
        reason="证据完整，同意 mock 审批。",
        decided_by="agent_001",
        expected_state_version=1,
        now=NOW,
    )
    duplicate = await service.decide(
        "approval_test_000001",
        decision=ApprovalDecision.APPROVED,
        reason="证据完整，同意 mock 审批。",
        decided_by="agent_001",
        expected_state_version=1,
        now=NOW,
    )
    assert first.status == ApprovalDecision.APPROVED.value
    assert first.state_version == 2
    assert duplicate.duplicate is True
    async with approval_factory() as session:
        ticket = await session.get(ServiceTicket, "ticket_approval_0001")
        assert ticket is not None
        assert ticket.approval_status == ApprovalDecision.APPROVED.value
        assert ticket.state_version == 2


@pytest.mark.asyncio
async def test_different_second_decision_fails_closed(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = ApprovalService(approval_factory)
    await service.decide(
        "approval_test_000001",
        decision=ApprovalDecision.REJECTED,
        reason="材料不符合要求。",
        decided_by="agent_001",
        expected_state_version=1,
        now=NOW,
    )
    with pytest.raises(ApprovalStateConflict, match="different decision"):
        await service.decide(
            "approval_test_000001",
            decision=ApprovalDecision.APPROVED,
            reason="重复点击但改成通过。",
            decided_by="agent_002",
            expected_state_version=1,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_expiry_worker_closes_pending_approval(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = ApprovalService(approval_factory)
    expired = await service.expire_due(now=NOW + timedelta(days=2))
    assert len(expired) == 1
    assert expired[0].status == ApprovalDecision.EXPIRED.value
    assert expired[0].decided_by == "system_expiry"
    assert await service.expire_due(now=NOW + timedelta(days=2)) == []
