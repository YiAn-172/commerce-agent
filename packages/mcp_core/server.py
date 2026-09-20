from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import Request
from starlette.responses import JSONResponse

from packages.contracts.common import ToolEnvelope
from packages.mcp_core.context import TrustedToolContext, context_from_mcp
from packages.mcp_core.errors import ToolFailure
from packages.mcp_core.runtime import failure_envelope

T = TypeVar("T")


async def invoke_mcp_tool(
    context: Context[Any, Any, Any],
    *,
    audience: str,
    operation: Callable[[TrustedToolContext], Awaitable[ToolEnvelope[T]]],
) -> ToolEnvelope[T]:
    try:
        trusted = context_from_mcp(context, expected_audience=audience)
    except ToolFailure as failure:
        return cast(ToolEnvelope[T], failure_envelope(failure))
    return await operation(trusted)


def close_tool_input_schemas(mcp: FastMCP[Any], *tool_names: str) -> None:
    """Reject undeclared MCP arguments and publish closed JSON schemas.

    FastMCP currently creates argument models with Pydantic's default ``extra=ignore``.
    Commerce tools carry identity only in trusted headers, so accepting arbitrary fields
    would silently discard spoofed identity parameters. Harden the generated models and
    their advertised schemas together.
    """
    manager = cast(Any, mcp)._tool_manager
    for tool_name in tool_names:
        tool = manager.get_tool(tool_name)
        if tool is None:
            raise ValueError(f"MCP tool is not registered: {tool_name}")
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config["extra"] = "forbid"
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema()


def register_health_routes(mcp: FastMCP[Any], engine: AsyncEngine, *, service_name: str) -> None:
    @mcp.custom_route("/health/live", methods=["GET"])  # type: ignore[untyped-decorator]
    async def live(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "alive",
                "service": service_name,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    @mcp.custom_route("/health/ready", methods=["GET"])  # type: ignore[untyped-decorator]
    async def ready(_: Request) -> JSONResponse:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                {"status": "not_ready", "service": service_name, "database": "failed"},
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ready",
                "service": service_name,
                "database": "ok",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
