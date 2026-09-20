from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from packages.contracts.intent import IntentPrediction
from packages.rag_core.models import RetrievalResult
from packages.rag_core.retriever import HybridRetriever


class HttpIntentGateway:
    def __init__(self, base_url: str = "http://intent-service:8001", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def predict(
        self, text: str, previous_user_text: str | None = None
    ) -> IntentPrediction:
        payload: dict[str, Any] = {"text": text}
        if previous_user_text:
            payload["previous_user_text"] = previous_user_text
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/predict", json=payload)
            response.raise_for_status()
        return IntentPrediction.model_validate_json(response.text)


class RagRetrievalGateway:
    def __init__(self, retriever: HybridRetriever) -> None:
        self.retriever = retriever

    async def retrieve(
        self,
        query: str,
        *,
        doc_types: list[str] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> RetrievalResult:
        return await self.retriever.retrieve(
            query,
            doc_types=doc_types,
            candidate_product_ids=candidate_product_ids,
        )


class McpToolGateway:
    TOOL_SERVICES = {
        "search_products": (
            "http://mcp-catalog:8101/mcp",
            "commerce-mcp-catalog",
            "catalog:read",
        ),
        "get_product_detail": (
            "http://mcp-catalog:8101/mcp",
            "commerce-mcp-catalog",
            "catalog:read",
        ),
        "check_inventory": (
            "http://mcp-catalog:8101/mcp",
            "commerce-mcp-catalog",
            "catalog:read",
        ),
        "list_recent_orders": ("http://mcp-order:8102/mcp", "commerce-mcp-order", "order:read"),
        "get_order_detail": ("http://mcp-order:8102/mcp", "commerce-mcp-order", "order:read"),
        "track_logistics": ("http://mcp-order:8102/mcp", "commerce-mcp-order", "order:read"),
        "check_after_sales_eligibility": (
            "http://mcp-after-sales:8103/mcp",
            "commerce-mcp-after-sales",
            "after-sales:read",
        ),
        "prefill_service_ticket": (
            "http://mcp-after-sales:8103/mcp",
            "commerce-mcp-after-sales",
            "after-sales:write",
        ),
    }

    def __init__(self, *, internal_token: str | None = None, timeout: float = 15.0) -> None:
        token = (
            internal_token
            or os.getenv("MCP_INTERNAL_TOKEN", "")
            or os.getenv("APP_SECRET", "")
        )
        self.timeout = timeout
        if not token:
            raise ValueError("MCP_INTERNAL_TOKEN is required")
        self.internal_token: str = token

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
        trace_id: str,
        request_id: str,
        deadline_ms: int,
    ) -> dict[str, Any]:
        if tool_name not in self.TOOL_SERVICES:
            raise ValueError(f"tool is not allowlisted: {tool_name}")
        url, audience, scope = self.TOOL_SERVICES[tool_name]
        headers = {
            "X-Internal-Token": self.internal_token,
            "X-Principal-Id": principal_id,
            "X-Scopes": scope,
            "X-Mcp-Audience": audience,
            "X-Trace-Id": trace_id,
            "X-Request-Id": f"{request_id}_{uuid4().hex[:8]}",
            "X-Deadline-Ms": str(deadline_ms),
        }
        async with (
            httpx.AsyncClient(headers=headers, timeout=self.timeout) as client,
            streamable_http_client(url, http_client=client) as streams,
        ):
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        structured = result.structuredContent
        if not isinstance(structured, dict):
            raise RuntimeError(f"MCP tool {tool_name} returned no structured content")
        return structured
