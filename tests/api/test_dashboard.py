from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.routes import router
from packages.agent_core.graph import GRAPH_VERSION
from packages.business.models import (
    Base,
    ChatSession,
    EvaluationResult,
    EvaluationRun,
    KnowledgeVersion,
    User,
)
from tests.approval_core.conftest import NOW


class FakeServices:
    def __init__(self, factory: Any) -> None:
        self.factory = factory


async def login(client: AsyncClient, principal_id: str, role: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/demo-login",
        json={"principal_id": principal_id, "role": role},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_session_list_is_customer_scoped_and_agent_can_see_queue() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add_all(
            [
                User(
                    id="usr_dashboard_0001",
                    external_ref="dashboard-1",
                    display_name="用户一",
                    masked_phone="138****0001",
                    region="华东",
                    created_at=NOW,
                ),
                User(
                    id="usr_dashboard_0002",
                    external_ref="dashboard-2",
                    display_name="用户二",
                    masked_phone="138****0002",
                    region="华北",
                    created_at=NOW,
                ),
                ChatSession(
                    id="ses_dashboard_00000001",
                    user_id="usr_dashboard_0001",
                    state_version=1,
                    graph_version=GRAPH_VERSION,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                ChatSession(
                    id="ses_dashboard_00000002",
                    user_id="usr_dashboard_0002",
                    state_version=1,
                    graph_version=GRAPH_VERSION,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        await database.commit()
    app = FastAPI()
    app.include_router(router)
    app.state.services = FakeServices(factory)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        customer_rows = await client.get(
            "/api/v1/sessions",
            headers=await login(client, "usr_dashboard_0001", "customer"),
        )
        agent_rows = await client.get(
            "/api/v1/sessions",
            headers=await login(client, "agent_dashboard_0001", "agent"),
        )
    await engine.dispose()

    assert customer_rows.status_code == 200
    assert [item["session_id"] for item in customer_rows.json()] == [
        "ses_dashboard_00000001"
    ]
    assert {item["session_id"] for item in agent_rows.json()} == {
        "ses_dashboard_00000001",
        "ses_dashboard_00000002",
    }


@pytest.mark.asyncio
async def test_agent_dashboard_returns_active_knowledge_and_recent_evaluation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add(
            KnowledgeVersion(
                id="kv_dashboard_0001",
                version="kb_dashboard_20260918",
                status="active",
                manifest_json={"document_count": 12},
                activated_at=NOW,
                created_at=NOW,
            )
        )
        database.add(
            EvaluationRun(
                id="eval_dashboard_0001",
                name="Dashboard smoke",
                manifest_sha256="d" * 64,
                status="completed",
                metrics_json={"accuracy": 0.975},
                created_at=NOW,
                completed_at=NOW,
            )
        )
        database.add(
            EvaluationResult(
                id="eval_result_dashboard_0001",
                evaluation_run_id="eval_dashboard_0001",
                case_id="case_dashboard_0001",
                category="routing",
                passed=True,
                score=Decimal("0.97500"),
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
        auth = await login(client, "agent_dashboard_0001", "agent")
        knowledge = await client.get("/api/v1/knowledge/status", headers=auth)
        evaluations = await client.get("/api/v1/evaluations", headers=auth)
    await engine.dispose()

    assert knowledge.status_code == 200
    assert knowledge.json()["active_version"] == "kb_dashboard_20260918"
    assert knowledge.json()["latest_version"] == "kb_dashboard_20260918"
    assert evaluations.status_code == 200
    assert evaluations.json()[0]["metrics"] == {"accuracy": 0.975}
    assert evaluations.json()[0]["passed_count"] == 1
