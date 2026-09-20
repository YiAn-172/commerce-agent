from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from packages.contracts.common import ErrorCode
from packages.mcp_core.errors import ToolFailure

PRINCIPAL_PATTERN = re.compile(r"^usr_[A-Za-z0-9_-]{6,64}$")
TRACE_PATTERN = re.compile(r"^tr_[A-Za-z0-9_-]{8,64}$")
REQUEST_PATTERN = re.compile(r"^req_[A-Za-z0-9_-]{8,64}$")
MAX_DEADLINE_MS = 60_000


@dataclass(frozen=True)
class TrustedToolContext:
    principal_id: str
    scopes: frozenset[str]
    audience: str
    trace_id: str
    request_id: str
    deadline_ms: int

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ToolFailure(
                ErrorCode.PERMISSION_DENIED,
                "当前调用缺少所需权限。",
            )


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value).strip() for key, value in headers.items()}


def _internal_token() -> str:
    token = os.getenv("MCP_INTERNAL_TOKEN") or os.getenv("APP_SECRET")
    if not token:
        raise ToolFailure(
            ErrorCode.DEPENDENCY_ERROR,
            "MCP 服务端内部认证未配置。",
        )
    return token


def context_from_headers(
    headers: Mapping[str, str],
    *,
    expected_audience: str,
    internal_token: str | None = None,
) -> TrustedToolContext:
    values = _normalized_headers(headers)
    presented_token = values.get("x-internal-token", "")
    expected_token = internal_token or _internal_token()
    if not presented_token or not secrets.compare_digest(presented_token, expected_token):
        raise ToolFailure(ErrorCode.PERMISSION_DENIED, "内部调用身份校验失败。")

    principal_id = values.get("x-principal-id", "")
    trace_id = values.get("x-trace-id", "")
    request_id = values.get("x-request-id", "")
    audience = values.get("x-mcp-audience", "")
    if not PRINCIPAL_PATTERN.fullmatch(principal_id):
        raise ToolFailure(ErrorCode.PERMISSION_DENIED, "可信主体信息缺失或无效。")
    if not TRACE_PATTERN.fullmatch(trace_id) or not REQUEST_PATTERN.fullmatch(request_id):
        raise ToolFailure(ErrorCode.INVALID_ARGUMENT, "请求追踪信息缺失或无效。")
    if audience != expected_audience:
        raise ToolFailure(ErrorCode.PERMISSION_DENIED, "请求受众与当前 MCP 服务不匹配。")

    raw_scopes = values.get("x-scopes", "")
    scopes = frozenset(value for value in re.split(r"[\s,]+", raw_scopes) if value)
    if not scopes:
        raise ToolFailure(ErrorCode.PERMISSION_DENIED, "可信权限范围缺失。")
    try:
        deadline_ms = int(values.get("x-deadline-ms", ""))
    except ValueError as error:
        raise ToolFailure(ErrorCode.INVALID_ARGUMENT, "请求 deadline 无效。") from error
    if not 1 <= deadline_ms <= MAX_DEADLINE_MS:
        raise ToolFailure(
            ErrorCode.INVALID_ARGUMENT,
            f"deadline_ms 必须在 1 到 {MAX_DEADLINE_MS} 之间。",
        )
    return TrustedToolContext(
        principal_id=principal_id,
        scopes=scopes,
        audience=audience,
        trace_id=trace_id,
        request_id=request_id,
        deadline_ms=deadline_ms,
    )


def context_from_mcp(context: Any, *, expected_audience: str) -> TrustedToolContext:
    try:
        request = context.request_context.request
        raw_headers = request.headers
        headers = cast(Mapping[str, str], raw_headers)
    except (AttributeError, ValueError) as error:
        raise ToolFailure(
            ErrorCode.PERMISSION_DENIED,
            "当前工具调用不包含可信 HTTP 请求上下文。",
        ) from error
    return context_from_headers(headers, expected_audience=expected_audience)
