import asyncio
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.demo_data import BASE_TIME
from packages.business.models import ApprovalTask, ServiceTicket
from packages.contracts.after_sales import AfterSalesRequestType, PrefillServiceTicketInput
from packages.mcp_core.after_sales import AfterSalesToolService
from tests.mcp.helpers import trusted_context

IDEMPOTENCY_KEY = "idem_p4_concurrent_000001"


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        ticket_ids = select(ServiceTicket.id).where(
            ServiceTicket.principal_id == "usr_demo_0005",
            ServiceTicket.idempotency_key == IDEMPOTENCY_KEY,
        )
        await session.execute(delete(ApprovalTask).where(ApprovalTask.ticket_id.in_(ticket_ids)))
        await session.execute(
            delete(ServiceTicket).where(
                ServiceTicket.principal_id == "usr_demo_0005",
                ServiceTicket.idempotency_key == IDEMPOTENCY_KEY,
            )
        )
        await session.commit()


async def test_twenty_concurrent_prefills_create_exactly_one_draft(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _cleanup(mysql_factory)
    service = AfterSalesToolService(
        mysql_factory,
        clock=lambda: BASE_TIME + timedelta(days=1),
    )
    context = trusted_context(
        "usr_demo_0005",
        "after-sales:read",
        "after-sales:write",
    )
    payload = PrefillServiceTicketInput(
        order_id="ord_demo_000005",
        item_id="item_demo_000005_1",
        request_type=AfterSalesRequestType.RETURN,
        reason_code="DO_NOT_WANT",
        description="联系电话 13800138000，邮箱 demo@example.com，仅用于脱敏测试。",
        idempotency_key=IDEMPOTENCY_KEY,
    )
    try:
        results = await asyncio.gather(
            *(service.prefill_service_ticket(context, payload) for _ in range(20))
        )
        assert all(result.ok and result.data is not None for result in results)
        ticket_ids = {result.data.ticket_id for result in results if result.data is not None}
        assert len(ticket_ids) == 1
        assert sum(bool(result.data and result.data.duplicate) for result in results) == 19

        async with mysql_factory() as session:
            ticket_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ServiceTicket)
                    .where(ServiceTicket.id.in_(ticket_ids))
                )
                or 0
            )
            approval_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ApprovalTask)
                    .where(ApprovalTask.ticket_id.in_(ticket_ids))
                )
                or 0
            )
            ticket = await session.scalar(
                select(ServiceTicket).where(ServiceTicket.id.in_(ticket_ids))
            )
        assert ticket_count == 1
        assert approval_count == 1
        assert ticket is not None
        assert ticket.description_redacted == ("联系电话 [PHONE]，邮箱 [EMAIL]，仅用于脱敏测试。")
    finally:
        await _cleanup(mysql_factory)
