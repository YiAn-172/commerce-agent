from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from packages.business.database import create_engine, session_factory
from packages.contracts.catalog import (
    CheckInventoryInput,
    InventorySnapshot,
    ProductDetail,
    ProductDetailInput,
    ProductSummary,
    SearchProductsInput,
)
from packages.contracts.common import ToolEnvelope
from packages.mcp_core.catalog import CatalogToolService
from packages.mcp_core.server import (
    close_tool_input_schemas,
    invoke_mcp_tool,
    register_health_routes,
)

AUDIENCE = "commerce-mcp-catalog"
engine = create_engine()
service = CatalogToolService(session_factory(engine))
mcp = FastMCP(
    "Commerce Catalog MCP",
    instructions="只返回数据库中的商品、价格和库存事实。",
    host="0.0.0.0",
    port=8101,
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)
register_health_routes(mcp, engine, service_name="mcp-catalog")


@mcp.tool()
async def get_product_detail(
    product_id: Annotated[str, Field(pattern=r"^prd_[A-Za-z0-9_-]{6,64}$")],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[ProductDetail]:
    """读取一个商品及其所有在售 SKU；不查询订单或用户数据。"""
    payload = ProductDetailInput(product_id=product_id)
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.get_product_detail(trusted, payload),
    )


@mcp.tool()
async def check_inventory(
    product_id: Annotated[str, Field(pattern=r"^prd_[A-Za-z0-9_-]{6,64}$")],
    sku_id: Annotated[str, Field(pattern=r"^sku_[A-Za-z0-9_-]{6,64}$")],
    region: Annotated[str, Field(min_length=2, max_length=40)],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[InventorySnapshot]:
    """读取指定商品 SKU 在指定区域的库存快照及 as_of。"""
    payload = CheckInventoryInput(product_id=product_id, sku_id=sku_id, region=region)
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.check_inventory(trusted, payload),
    )


@mcp.tool()
async def search_products(
    query: Annotated[str, Field(min_length=1, max_length=200)],
    ctx: Context[Any, Any, Any],
    category: Annotated[str | None, Field(max_length=60)] = None,
    brand: Annotated[str | None, Field(max_length=60)] = None,
    price_min: Annotated[Decimal | None, Field(ge=0)] = None,
    price_max: Annotated[Decimal | None, Field(ge=0)] = None,
    filters: dict[str, str | int | float | bool] | None = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 10,
    sort: Literal["relevance", "price_asc", "price_desc", "rating"] = "relevance",
) -> ToolEnvelope[list[ProductSummary]]:
    """按关键词和结构化条件搜索商品 SKU，返回可核验价格快照。"""
    payload = SearchProductsInput(
        query=query,
        category=category,
        brand=brand,
        price_min=price_min,
        price_max=price_max,
        filters=filters or {},
        limit=limit,
        sort=sort,
    )
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.search_products(trusted, payload),
    )


close_tool_input_schemas(
    mcp,
    "get_product_detail",
    "check_inventory",
    "search_products",
)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
