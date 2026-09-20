import asyncio
from dataclasses import replace

from packages.contracts.common import ErrorCode
from packages.mcp_core.runtime import execute_tool
from tests.mcp.helpers import trusted_context


async def _slow_operation() -> str:
    await asyncio.sleep(0.05)
    return "late"


async def test_read_timeout_is_bounded_and_retryable() -> None:
    context = replace(
        trusted_context("usr_demo_0001", "order:read"),
        deadline_ms=1,
    )
    result = await execute_tool(
        name="slow_read",
        context=context,
        required_scope="order:read",
        operation=_slow_operation,
    )
    assert result.error is not None
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retryable is True


async def test_write_timeout_is_never_marked_retryable() -> None:
    context = replace(
        trusted_context("usr_demo_0001", "after-sales:write"),
        deadline_ms=1,
    )
    result = await execute_tool(
        name="slow_write",
        context=context,
        required_scope="after-sales:write",
        operation=_slow_operation,
    )
    assert result.error is not None
    assert result.error.code == ErrorCode.TIMEOUT
    assert result.error.retryable is False
