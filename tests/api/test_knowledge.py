from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.routes import router
from packages.business.models import Base, EvaluationResult, EvaluationRun
from tests.approval_core.conftest import NOW


class FakeServices:
    def __init__(self, factory: Any) -> None:
        self.factory = factory


@pytest.mark.asyncio
async def test_knowledge_document_is_idempotent_and_reindex_job_is_persisted() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.state.services = FakeServices(factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/demo-login",
            json={"principal_id": "admin_demo_0001", "role": "admin"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        payload = {
            "document_type": "policy",
            "title": "演示补充退货规则",
            "source_uri": "demo://uploaded/policy-1",
            "content": "演示补充规则：申请前必须通过售后资格工具复检。",
        }
        first = await client.post("/api/v1/knowledge/documents", headers=auth, json=payload)
        duplicate = await client.post(
            "/api/v1/knowledge/documents", headers=auth, json=payload
        )
        document_id = first.json()["document_id"]
        missing = await client.post(
            "/api/v1/knowledge/reindex",
            headers=auth,
            json={"document_ids": ["kdoc_missing_0001"], "target_version": "kb_20260918_002"},
        )
        queued = await client.post(
            "/api/v1/knowledge/reindex",
            headers=auth,
            json={"document_ids": [document_id], "target_version": "kb_20260918_002"},
        )
    await engine.dispose()

    assert first.status_code == 201
    assert first.json()["duplicate"] is False
    assert duplicate.json()["document_id"] == document_id
    assert duplicate.json()["duplicate"] is True
    assert missing.status_code == 422
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert queued.json()["document_count"] == 1


@pytest.mark.asyncio
async def test_evaluation_route_returns_persisted_metrics() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add(
            EvaluationRun(
                id="eval_api_000001",
                name="API 演示评测",
                manifest_sha256="a" * 64,
                status="completed",
                metrics_json={"accuracy": 0.95},
                created_at=NOW,
                completed_at=NOW,
            )
        )
        database.add(
            EvaluationResult(
                id="eval_result_api_000001",
                evaluation_run_id="eval_api_000001",
                case_id="case_0001",
                category="routing",
                passed=True,
                score=Decimal("0.95000"),
                error_type=None,
                details_json={},
            )
        )
        await database.commit()
    app = FastAPI()
    app.include_router(router)
    app.state.services = FakeServices(factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/demo-login",
            json={"principal_id": "admin_demo_0001", "role": "admin"},
        )
        response = await client.get(
            "/api/v1/evaluations/eval_api_000001",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
    await engine.dispose()

    assert response.status_code == 200
    assert response.json()["metrics"] == {"accuracy": 0.95}
    assert response.json()["result_count"] == 1
    assert response.json()["passed_count"] == 1
