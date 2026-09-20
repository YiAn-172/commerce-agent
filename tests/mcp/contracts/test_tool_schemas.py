from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from services.mcp_after_sales.server import mcp as after_sales_mcp
from services.mcp_catalog.server import mcp as catalog_mcp
from services.mcp_order.server import mcp as order_mcp

EXPECTED_TOOLS = {
    "get_product_detail",
    "check_inventory",
    "search_products",
    "get_order_detail",
    "list_recent_orders",
    "track_logistics",
    "check_after_sales_eligibility",
    "prefill_service_ticket",
}
FORBIDDEN_IDENTITY_FIELDS = {
    "user_id",
    "principal_id",
    "scopes",
    "audience",
    "trace_id",
    "request_id",
    "deadline_ms",
}


async def _schemas(server: FastMCP[Any]) -> dict[str, dict[str, Any]]:
    return {tool.name: tool.inputSchema for tool in await server.list_tools()}


async def test_exactly_eight_versioned_tool_schemas_are_exposed() -> None:
    schemas: dict[str, dict[str, Any]] = {}
    for server in (catalog_mcp, order_mcp, after_sales_mcp):
        tools = await server.list_tools()
        schemas.update({tool.name: tool.inputSchema for tool in tools})
        for tool in tools:
            assert tool.outputSchema is not None
            assert tool.outputSchema.get("additionalProperties") is False
            assert {"ok", "data", "error", "meta"}.issubset(tool.outputSchema["properties"])
    assert set(schemas) == EXPECTED_TOOLS
    assert len(schemas) == 8

    for schema in schemas.values():
        assert FORBIDDEN_IDENTITY_FIELDS.isdisjoint(schema.get("properties", {}))
        assert schema.get("additionalProperties") is False


async def test_schema_constraints_are_machine_readable() -> None:
    catalog = await _schemas(catalog_mcp)
    order = await _schemas(order_mcp)
    after_sales = await _schemas(after_sales_mcp)

    assert catalog["search_products"]["properties"]["limit"]["maximum"] == 20
    assert order["list_recent_orders"]["properties"]["limit"]["maximum"] == 10
    assert "pattern" in after_sales["prefill_service_ticket"]["properties"]["idempotency_key"]


async def test_generated_argument_models_reject_undeclared_fields() -> None:
    for server in (catalog_mcp, order_mcp, after_sales_mcp):
        for tool in await server.list_tools():
            registered = server._tool_manager.get_tool(tool.name)
            assert registered is not None
            fields = registered.fn_metadata.arg_model.model_fields
            payload: dict[str, Any] = {}
            for name, field in fields.items():
                if field.is_required():
                    schema = field.annotation
                    if name.endswith("_id"):
                        payload[name] = "invalid_but_schema_target_123456"
                    elif schema is str:
                        payload[name] = "value"
            payload["principal_id"] = "usr_attacker_0001"
            try:
                registered.fn_metadata.arg_model.model_validate(payload)
            except ValidationError as error:
                assert any(item["type"] == "extra_forbidden" for item in error.errors())
            else:
                raise AssertionError(f"{tool.name} accepted an undeclared identity field")
