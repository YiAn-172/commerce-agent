from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from packages.business.database import create_engine, session_factory
from packages.contracts.common import ToolEnvelope
from packages.contracts.order import (
    GetOrderDetailInput,
    ListRecentOrdersInput,
    LogisticsTimeline,
    OrderDetail,
    RecentOrderPage,
    TrackLogisticsInput,
)
from packages.mcp_core.order import OrderToolService
from packages.mcp_core.server import (
    close_tool_input_schemas,
    invoke_mcp_tool,
    register_health_routes,
)

AUDIENCE = "commerce-mcp-order"
engine = create_engine()
service = OrderToolService(session_factory(engine))
mcp = FastMCP(
    "Commerce Order MCP",
    instructions="订单查询始终按服务端可信主体过滤，不泄露其他用户订单。",
    host="0.0.0.0",
    port=8102,
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)
register_health_routes(mcp, engine, service_name="mcp-order")


@mcp.tool()
async def get_order_detail(
    order_id: Annotated[str, Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[OrderDetail]:
    """读取当前可信主体拥有的订单和成交快照。"""
    payload = GetOrderDetailInput(order_id=order_id)
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.get_order_detail(trusted, payload),
    )


@mcp.tool()
async def list_recent_orders(
    ctx: Context[Any, Any, Any],
    limit: Annotated[int, Field(ge=1, le=10)] = 3,
    cursor: Annotated[str | None, Field(max_length=200)] = None,
) -> ToolEnvelope[RecentOrderPage]:
    """分页列出当前可信主体最近的订单，不接受用户身份参数。"""
    payload = ListRecentOrdersInput(limit=limit, cursor=cursor)
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.list_recent_orders(trusted, payload),
    )


@mcp.tool()
async def track_logistics(
    order_id: Annotated[str, Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[LogisticsTimeline]:
    """读取当前可信主体订单的脱敏物流轨迹。"""
    payload = TrackLogisticsInput(order_id=order_id)
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.track_logistics(trusted, payload),
    )


close_tool_input_schemas(
    mcp,
    "get_order_detail",
    "list_recent_orders",
    "track_logistics",
)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
