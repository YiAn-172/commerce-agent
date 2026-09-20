from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.contracts.after_sales import (
    AfterSalesEligibilityInput,
    AfterSalesRequestType,
)
from packages.contracts.common import ErrorCode
from packages.contracts.order import GetOrderDetailInput, TrackLogisticsInput
from packages.mcp_core.after_sales import AfterSalesToolService
from packages.mcp_core.order import OrderToolService
from tests.mcp.helpers import trusted_context


async def test_cross_user_order_lookup_returns_no_order_fields(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = OrderToolService(mysql_factory)
    attacker = trusted_context("usr_demo_0002", "order:read")
    result = await service.get_order_detail(
        attacker, GetOrderDetailInput(order_id="ord_demo_000001")
    )

    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == ErrorCode.NOT_FOUND
    serialized = result.model_dump_json()
    assert "total_amount" not in serialized
    assert "item_demo" not in serialized
    assert "usr_demo_0001" not in serialized


async def test_cross_user_logistics_and_after_sales_are_indistinguishable_from_missing(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    attacker = trusted_context(
        "usr_demo_0006",
        "order:read",
        "after-sales:read",
    )
    logistics = await OrderToolService(mysql_factory).track_logistics(
        attacker,
        TrackLogisticsInput(order_id="ord_demo_000004"),
    )
    eligibility = await AfterSalesToolService(mysql_factory).check_eligibility(
        attacker,
        AfterSalesEligibilityInput(
            order_id="ord_demo_000005",
            item_id="item_demo_000005_1",
            request_type=AfterSalesRequestType.RETURN,
            reason_code="DO_NOT_WANT",
        ),
    )

    assert logistics.error is not None
    assert logistics.error.code == ErrorCode.NOT_FOUND
    assert eligibility.error is not None
    assert eligibility.error.code == ErrorCode.NOT_FOUND


async def test_missing_scope_fails_before_returning_data(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    no_scope = trusted_context("usr_demo_0001", "catalog:read")
    result = await OrderToolService(mysql_factory).get_order_detail(
        no_scope,
        GetOrderDetailInput(order_id="ord_demo_000001"),
    )
    assert result.error is not None
    assert result.error.code == ErrorCode.PERMISSION_DENIED
