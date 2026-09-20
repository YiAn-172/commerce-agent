import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from apps.api.routes import router
from apps.api.services import ApplicationServices
from apps.api.settings import get_settings
from packages.api_core.events import RedisEventStore
from packages.business.models import KnowledgeVersion


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    services = ApplicationServices(settings, redis)
    application.state.services = services
    application.state.redis = redis
    application.state.event_store = RedisEventStore(
        redis,
        retention_seconds=settings.sse_retention_seconds,
    )
    try:
        yield
    finally:
        await redis.aclose()
        await services.close()


app = FastAPI(
    title="CommerceAgent API",
    version="0.1.0",
    description="E-commerce customer-service and shopping Agent gateway",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, object]:
    return {"status": "alive", "timestamp": datetime.now(UTC).isoformat()}


async def dependency_http_check(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(url)
        return {"status": "ok" if response.is_success else "failed", "code": response.status_code}
    except httpx.HTTPError as error:
        return {"status": "failed", "error": type(error).__name__}


@app.get("/health/ready", tags=["health"])
async def ready(request: Request) -> JSONResponse:
    settings = get_settings()
    services: ApplicationServices = request.app.state.services
    redis: Redis = request.app.state.redis
    checks: dict[str, Any] = {}
    try:
        async with services.factory() as database:
            await database.execute(text("SELECT 1"))
            active_knowledge = await database.scalar(
                select(KnowledgeVersion.version)
                .where(KnowledgeVersion.status == "active")
                .order_by(KnowledgeVersion.activated_at.desc())
            )
        checks["mysql"] = {"status": "ok"}
        checks["knowledge_version"] = {
            "status": "ok" if active_knowledge else "failed",
            "version": active_knowledge,
        }
    except SQLAlchemyError as error:
        checks["mysql"] = {"status": "failed", "error": type(error).__name__}
        checks["knowledge_version"] = {"status": "failed", "version": None}
    try:
        checks["redis"] = {"status": "ok" if await redis.ping() else "failed"}
    except RedisError as error:
        checks["redis"] = {"status": "failed", "error": type(error).__name__}

    urls = {
        "intent_service": "http://intent-service:8001/health/ready",
        "mcp_catalog": "http://mcp-catalog:8101/health/ready",
        "mcp_order": "http://mcp-order:8102/health/ready",
        "mcp_after_sales": "http://mcp-after-sales:8103/health/ready",
        "elasticsearch": "http://elasticsearch:9200/_cluster/health",
        "milvus": "http://milvus:9091/healthz",
    }
    results = await asyncio.gather(*(dependency_http_check(url) for url in urls.values()))
    checks.update(dict(zip(urls, results, strict=True)))
    is_ready = all(item.get("status") == "ok" for item in checks.values())
    payload = {
        "status": "ready" if is_ready else "not_ready",
        "environment": settings.app_env,
        "model": settings.deepseek_model,
        "checks": checks,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return JSONResponse(payload, status_code=200 if is_ready else 503)


@app.get("/api/v1/meta", tags=["system"])
async def meta() -> dict[str, object]:
    settings = get_settings()
    return {
        "name": "CommerceAgent",
        "version": app.version,
        "limits": {
            "max_graph_steps": settings.max_graph_steps,
            "max_tool_calls": settings.max_tool_calls,
            "max_llm_calls": settings.max_llm_calls,
        },
    }
