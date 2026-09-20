from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@dataclass(frozen=True)
class SmokeCase:
    service: str
    url: str
    audience: str
    scopes: str
    expected_tools: frozenset[str]
    call_name: str
    arguments: dict[str, Any]


CASES = (
    SmokeCase(
        service="mcp-catalog",
        url="http://mcp-catalog:8101/mcp",
        audience="commerce-mcp-catalog",
        scopes="catalog:read",
        expected_tools=frozenset({"get_product_detail", "check_inventory", "search_products"}),
        call_name="get_product_detail",
        arguments={"product_id": "prd_demo_000001"},
    ),
    SmokeCase(
        service="mcp-order",
        url="http://mcp-order:8102/mcp",
        audience="commerce-mcp-order",
        scopes="order:read",
        expected_tools=frozenset({"get_order_detail", "list_recent_orders", "track_logistics"}),
        call_name="get_order_detail",
        arguments={"order_id": "ord_demo_000001"},
    ),
    SmokeCase(
        service="mcp-after-sales",
        url="http://mcp-after-sales:8103/mcp",
        audience="commerce-mcp-after-sales",
        scopes="after-sales:read after-sales:write",
        expected_tools=frozenset({"check_after_sales_eligibility", "prefill_service_ticket"}),
        call_name="check_after_sales_eligibility",
        arguments={
            "order_id": "ord_demo_000005",
            "item_id": "item_demo_000005_1",
            "request_type": "return",
            "reason_code": "DO_NOT_WANT",
        },
    ),
)


async def run_case(case: SmokeCase, token: str) -> dict[str, Any]:
    headers = {
        "X-Internal-Token": token,
        "X-Principal-Id": "usr_demo_0005" if case.service == "mcp-after-sales" else "usr_demo_0001",
        "X-Scopes": case.scopes,
        "X-Mcp-Audience": case.audience,
        "X-Trace-Id": f"tr_smoke_{case.service.replace('-', '_')}",
        "X-Request-Id": f"req_smoke_{case.service.replace('-', '_')}",
        "X-Deadline-Ms": "10000",
    }
    async with (
        httpx.AsyncClient(headers=headers, timeout=15) as http_client,
        streamable_http_client(case.url, http_client=http_client) as streams,
    ):
        read_stream, write_stream, _ = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            if tool_names != case.expected_tools:
                raise RuntimeError(f"{case.service} tool mismatch: {sorted(tool_names)}")
            result = await session.call_tool(case.call_name, case.arguments)
            if result.isError:
                raise RuntimeError(f"{case.service} returned MCP protocol error")
            structured = result.structuredContent
            if not isinstance(structured, dict) or structured.get("ok") is not True:
                raise RuntimeError(f"{case.service} returned invalid tool envelope: {structured}")
            return {
                "service": case.service,
                "tools": sorted(tool_names),
                "call": case.call_name,
                "ok": True,
                "trace_id": structured["meta"]["trace_id"],
            }


async def main_async() -> None:
    token = os.getenv("MCP_INTERNAL_TOKEN") or os.getenv("APP_SECRET")
    if not token:
        raise RuntimeError("MCP_INTERNAL_TOKEN or APP_SECRET is required")
    results = await asyncio.gather(*(run_case(case, token) for case in CASES))
    print(json.dumps({"status": "passed", "services": results}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
