from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.demo_data import BASE_TIME
from packages.contracts.after_sales import (
    AfterSalesEligibilityInput,
    AfterSalesRequestType,
)
from packages.contracts.catalog import (
    CheckInventoryInput,
    ProductDetailInput,
    SearchProductsInput,
)
from packages.contracts.order import (
    GetOrderDetailInput,
    ListRecentOrdersInput,
    TrackLogisticsInput,
)
from packages.mcp_core.after_sales import AfterSalesToolService
from packages.mcp_core.catalog import CatalogToolService
from packages.mcp_core.order import OrderToolService
from tests.mcp.helpers import trusted_context


async def test_catalog_tools_return_database_snapshots(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = CatalogToolService(mysql_factory)
    context = trusted_context("usr_demo_0001", "catalog:read")

    detail = await service.get_product_detail(
        context, ProductDetailInput(product_id="prd_demo_000001")
    )
    inventory = await service.check_inventory(
        context,
        CheckInventoryInput(
            product_id="prd_demo_000001",
            sku_id="sku_demo_000001",
            region="华东",
        ),
    )
    search = await service.search_products(
        context,
        SearchProductsInput(query="耳机", category="headphones", limit=5),
    )

    assert detail.ok and detail.data is not None
    assert len(detail.data.skus) == 4
    assert inventory.ok and inventory.data is not None
    assert inventory.data.available_quantity == 3
    assert search.ok and search.data
    assert all(item.category == "headphones" for item in search.data)
    assert all(item.price_snapshot_id for item in search.data)


async def test_order_tools_return_owned_snapshots_and_cursor(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = OrderToolService(mysql_factory)
    order_context = trusted_context("usr_demo_0001", "order:read")
    logistics_context = trusted_context("usr_demo_0004", "order:read")

    detail = await service.get_order_detail(
        order_context, GetOrderDetailInput(order_id="ord_demo_000001")
    )
    recent = await service.list_recent_orders(order_context, ListRecentOrdersInput(limit=2))
    logistics = await service.track_logistics(
        logistics_context, TrackLogisticsInput(order_id="ord_demo_000004")
    )

    assert detail.ok and detail.data is not None
    assert detail.data.order_id == "ord_demo_000001"
    assert detail.data.items
    assert recent.ok and recent.data is not None
    assert len(recent.data.orders) == 2
    assert recent.data.next_cursor is not None
    assert logistics.ok and logistics.data is not None
    assert 2 <= len(logistics.data.events) <= 6


async def test_after_sales_rule_is_recomputed_from_database(
    mysql_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = AfterSalesToolService(
        mysql_factory,
        clock=lambda: BASE_TIME + timedelta(days=1),
    )
    context = trusted_context("usr_demo_0005", "after-sales:read")
    result = await service.check_eligibility(
        context,
        AfterSalesEligibilityInput(
            order_id="ord_demo_000005",
            item_id="item_demo_000005_1",
            request_type=AfterSalesRequestType.RETURN,
            reason_code="DO_NOT_WANT",
        ),
    )

    assert result.ok and result.data is not None
    assert result.data.eligible is True
    assert result.data.reason_code == "ELIGIBLE"
    assert result.data.requires_human_approval is True
