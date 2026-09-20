from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast
from uuid import uuid4

from openai import OpenAIError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.api.schemas import ChatRequest, ChatResponse
from apps.api.settings import Settings
from packages.agent_core.deepseek import DeepSeekAdapter
from packages.agent_core.gateways import HttpIntentGateway, McpToolGateway, RagRetrievalGateway
from packages.agent_core.graph import GRAPH_VERSION, AgentContext, initial_state
from packages.agent_core.persistence import MySQLTraceSink
from packages.agent_core.runtime import open_agent_runner
from packages.approval_core.revalidation import McpApprovalRevalidator
from packages.approval_core.service import ApprovalService
from packages.approval_core.workflow import (
    ApprovalRunner,
    ApprovalWorkflowContext,
    open_approval_runner,
)
from packages.business.database import create_engine, session_factory
from packages.rag_core.config import load_rag_config
from packages.rag_core.retriever import HybridRetriever


class ChatService(Protocol):
    async def run(
        self,
        request: ChatRequest,
        *,
        principal_id: str,
        state_version: int,
    ) -> ChatResponse: ...


class LiveChatService:
    def __init__(
        self,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis | None = None,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.redis = redis
        self.trace_sink = MySQLTraceSink(factory)

    def _faq_cache_key(self, text: str, session_id: str) -> str | None:
        normalized = "".join(text.lower().split())
        if normalized not in {"手机保修政策是什么"}:
            return None
        material = (
            f"{GRAPH_VERSION}:{self.settings.deepseek_model}:{session_id}:{normalized}"
        ).encode()
        return f"commerce:faq:v1:{hashlib.sha256(material).hexdigest()}"

    async def run(
        self,
        request: ChatRequest,
        *,
        principal_id: str,
        state_version: int,
    ) -> ChatResponse:
        if self.settings.deepseek_api_key is None:
            raise RuntimeError("DEEPSEEK_API_KEY is required for live chat")
        request_id = request.request_id or f"req_{uuid4().hex}"
        trace_id = f"tr_{uuid4().hex}"
        cache_key = self._faq_cache_key(request.text, request.session_id)
        if cache_key is not None and self.redis is not None:
            try:
                cached_raw = await self.redis.get(cache_key)
                if cached_raw is not None:
                    cached = json.loads(cached_raw)
                    cached_state = initial_state(
                        text=request.text,
                        session_id=request.session_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        principal_id=principal_id,
                        state_version=state_version,
                    )
                    cached_state.update(
                        {
                            "status": str(cached["status"]),
                            "route": cached.get("route"),
                            "route_source": "redis_faq_cache",
                            "final_answer": str(cached["answer"]),
                            "citations": list(cached.get("citations", [])),
                        }
                    )
                    await self.trace_sink.persist(cast(dict[str, Any], cached_state))
                    return ChatResponse(
                        session_id=request.session_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        status=str(cached["status"]),
                        answer=str(cached["answer"]),
                        state_version=state_version + 1,
                        route=cached.get("route"),
                        citations=list(cached.get("citations", [])),
                        cache_status="HIT",
                    )
            except (RedisError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                # Cache is an optimization and never a source of authority. A malformed
                # or unavailable entry falls through to the full production graph.
                pass
        retriever = HybridRetriever(load_rag_config("configs/rag/rag_v1.yaml"))
        try:
            context = AgentContext(
                intent=HttpIntentGateway(),
                llm=DeepSeekAdapter(
                    api_key=self.settings.deepseek_api_key.get_secret_value(),
                    base_url=self.settings.deepseek_base_url,
                    model=self.settings.deepseek_model,
                    timeout_seconds=self.settings.deepseek_timeout_seconds,
                    max_retries=self.settings.deepseek_max_retries,
                    temperature=self.settings.deepseek_temperature,
                ),
                retriever=RagRetrievalGateway(retriever),
                tools=McpToolGateway(),
                trace_sink=self.trace_sink,
            )
            state = initial_state(
                text=request.text,
                session_id=request.session_id,
                request_id=request_id,
                trace_id=trace_id,
                principal_id=principal_id,
                state_version=state_version,
            )
            async with open_agent_runner(self.settings.checkpoint_db, context) as runner:
                try:
                    result = await runner.invoke(state)
                except OpenAIError:
                    # External model account/network failures must not become an
                    # unhandled 500 or trigger tools. Persist an auditable,
                    # fail-closed terminal response and let readiness/reporting
                    # continue to distinguish this from a successful generation.
                    state.update(
                        {
                            "status": "dependency_failed",
                            "route": "safe_reply",
                            "route_source": "deepseek_dependency_fallback",
                            "final_answer": (
                                "当前智能生成服务暂时不可用，请稍后重试；"
                                "订单、售后或退款操作均未执行。"
                            ),
                        }
                    )
                    await self.trace_sink.persist(cast(dict[str, Any], state))
                    return ChatResponse(
                        session_id=request.session_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        status="dependency_failed",
                        answer=str(state["final_answer"]),
                        state_version=state_version + 1,
                        route="safe_reply",
                        cache_status="BYPASS",
                    )
            tool_trace = [
                {
                    "tool_name": str(item.get("tool_name", "unknown")),
                    "ok": bool(item.get("ok", False)),
                    "duration_ms": int(item.get("duration_ms", 0)),
                    "error_code": item.get("error_code"),
                    "result_summary": dict(item.get("result_summary", {})),
                }
                for item in result.get("tool_calls", [])
                if isinstance(item, dict)
            ]
            response = ChatResponse(
                session_id=request.session_id,
                request_id=request_id,
                trace_id=trace_id,
                status=str(result.get("status", "completed")),
                answer=str(result.get("final_answer", "")),
                state_version=int(result.get("state_version", state_version)),
                route=result.get("route"),
                citations=list(result.get("citations", [])),
                products=list(result.get("candidate_products", [])),
                tool_trace=tool_trace,
                cache_status=(
                    "MISS" if cache_key is not None and self.redis is not None else "BYPASS"
                ),
            )
            if cache_key is not None and self.redis is not None:
                try:
                    await self.redis.set(
                        cache_key,
                        json.dumps(
                            {
                                "status": response.status,
                                "route": response.route,
                                "answer": response.answer,
                                "citations": response.citations,
                            },
                            ensure_ascii=False,
                        ),
                        ex=self.settings.faq_cache_ttl_seconds,
                    )
                except RedisError:
                    response.cache_status = "BYPASS"
            return response
        finally:
            await retriever.close()


class ApplicationServices:
    def __init__(self, settings: Settings, redis: Redis | None = None) -> None:
        self.settings = settings
        self.engine: AsyncEngine = create_engine()
        self.factory = session_factory(self.engine)
        self.chat: ChatService = LiveChatService(settings, self.factory, redis)
        self.approvals = ApprovalService(self.factory)

    @asynccontextmanager
    async def approval_runner(self) -> AsyncIterator[ApprovalRunner]:
        tools = McpToolGateway()
        revalidator = McpApprovalRevalidator(self.factory, tools)
        context = ApprovalWorkflowContext(service=self.approvals, revalidator=revalidator)
        async with open_approval_runner(
            self.settings.approval_checkpoint_db,
            context,
        ) as runner:
            yield runner

    async def close(self) -> None:
        await self.engine.dispose()


def state_summary(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    return dict(result)
