from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from packages.contracts.common import ErrorCode, ErrorDetail, ToolEnvelope, ToolMeta
from packages.mcp_core.context import TrustedToolContext
from packages.mcp_core.errors import ToolFailure

T = TypeVar("T")
LOGGER = logging.getLogger("commerce_agent.mcp")


def _meta(*, started: float, trace_id: str | None = None) -> ToolMeta:
    return ToolMeta(
        tool_call_id=f"tc_{uuid4().hex}",
        trace_id=trace_id or f"tr_{uuid4().hex}",
        as_of=datetime.now(UTC),
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
    )


def failure_envelope(
    failure: ToolFailure,
    *,
    started: float | None = None,
    trace_id: str | None = None,
) -> ToolEnvelope[Any]:
    return ToolEnvelope[Any](
        ok=False,
        error=ErrorDetail(
            code=failure.code,
            message=failure.message,
            retryable=failure.retryable,
        ),
        meta=_meta(started=started or time.perf_counter(), trace_id=trace_id),
    )


async def execute_tool(
    *,
    name: str,
    context: TrustedToolContext,
    required_scope: str,
    operation: Callable[[], Awaitable[T]],
) -> ToolEnvelope[T]:
    started = time.perf_counter()
    error_code: str | None = None
    try:
        context.require_scope(required_scope)
        async with asyncio.timeout(context.deadline_ms / 1000):
            result = await operation()
        return ToolEnvelope[T](
            ok=True,
            data=result,
            meta=_meta(started=started, trace_id=context.trace_id),
        )
    except TimeoutError:
        timeout_failure = ToolFailure(
            ErrorCode.TIMEOUT,
            "工具调用超过服务端 deadline。",
            retryable=required_scope.endswith(":read"),
        )
        error_code = timeout_failure.code.value
        return failure_envelope(timeout_failure, started=started, trace_id=context.trace_id)
    except ToolFailure as caught_failure:
        error_code = caught_failure.code.value
        return failure_envelope(caught_failure, started=started, trace_id=context.trace_id)
    except SQLAlchemyError:
        LOGGER.exception("MCP database dependency failed", extra={"tool_name": name})
        dependency_failure = ToolFailure(
            ErrorCode.DEPENDENCY_ERROR,
            "业务数据库暂时不可用。",
            retryable=required_scope.endswith(":read"),
        )
        error_code = dependency_failure.code.value
        return failure_envelope(dependency_failure, started=started, trace_id=context.trace_id)
    finally:
        principal_hash = hashlib.sha256(context.principal_id.encode()).hexdigest()[:12]
        LOGGER.info(
            json.dumps(
                {
                    "event": "mcp_tool_call",
                    "tool_name": name,
                    "trace_id": context.trace_id,
                    "request_id": context.request_id,
                    "principal_hash": principal_hash,
                    "error_code": error_code,
                    "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
                },
                ensure_ascii=False,
            )
        )
