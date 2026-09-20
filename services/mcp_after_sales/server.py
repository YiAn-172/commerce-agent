from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from packages.business.database import create_engine, session_factory
from packages.contracts.after_sales import (
    AfterSalesEligibilityInput,
    AfterSalesEligibilityResult,
    AfterSalesRequestType,
    PrefillServiceTicketInput,
    ServiceTicketDraft,
)
from packages.contracts.common import ToolEnvelope
from packages.mcp_core.after_sales import AfterSalesToolService
from packages.mcp_core.server import (
    close_tool_input_schemas,
    invoke_mcp_tool,
    register_health_routes,
)

AUDIENCE = "commerce-mcp-after-sales"
engine = create_engine()
service = AfterSalesToolService(session_factory(engine))
mcp = FastMCP(
    "Commerce After-sales MCP",
    instructions="重新执行确定性售后规则；写操作只生成 mock 审批草稿。",
    host="0.0.0.0",
    port=8103,
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)
register_health_routes(mcp, engine, service_name="mcp-after-sales")


@mcp.tool()
async def check_after_sales_eligibility(
    order_id: Annotated[str, Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")],
    item_id: Annotated[str, Field(pattern=r"^item_[A-Za-z0-9_-]{6,64}$")],
    request_type: AfterSalesRequestType,
    reason_code: Annotated[str, Field(min_length=2, max_length=50)],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[AfterSalesEligibilityResult]:
    """按订单快照和当前规则检查退换修或取消资格。"""
    payload = AfterSalesEligibilityInput(
        order_id=order_id,
        item_id=item_id,
        request_type=request_type,
        reason_code=reason_code,
    )
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.check_eligibility(trusted, payload),
    )


@mcp.tool()
async def prefill_service_ticket(
    order_id: Annotated[str, Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")],
    item_id: Annotated[str, Field(pattern=r"^item_[A-Za-z0-9_-]{6,64}$")],
    request_type: AfterSalesRequestType,
    reason_code: Annotated[str, Field(min_length=2, max_length=50)],
    description: Annotated[str, Field(min_length=1, max_length=1000)],
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")],
    ctx: Context[Any, Any, Any],
) -> ToolEnvelope[ServiceTicketDraft]:
    """幂等创建售后预填草稿和 mock 审批；不会执行真实资金动作。"""
    payload = PrefillServiceTicketInput(
        order_id=order_id,
        item_id=item_id,
        request_type=request_type,
        reason_code=reason_code,
        description=description,
        idempotency_key=idempotency_key,
    )
    return await invoke_mcp_tool(
        ctx,
        audience=AUDIENCE,
        operation=lambda trusted: service.prefill_service_ticket(trusted, payload),
    )


close_tool_input_schemas(
    mcp,
    "check_after_sales_eligibility",
    "prefill_service_ticket",
)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
