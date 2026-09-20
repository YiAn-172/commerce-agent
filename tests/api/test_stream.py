from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.routes import router
from apps.api.schemas import ChatRequest, ChatResponse
from packages.agent_core.graph import GRAPH_VERSION
from packages.api_core.events import SseEvent, sanitize_payload
from packages.business.models import Base, ChatSession, User
from tests.approval_core.conftest import NOW


class MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[SseEvent] = []

    async def append(
        self,
        *,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> SseEvent:
        event = SseEvent(
            event_id=f"evt_test_{len(self.events) + 1:08d}",
            session_id=session_id,
            run_id=run_id,
            sequence=len(self.events) + 1,
            timestamp=NOW,
            type=event_type,
            payload=sanitize_payload(payload),
        )
        self.events.append(event)
        return event

    async def history(
        self, session_id: str, *, after_event_id: str | None = None
    ) -> list[SseEvent]:
        rows = [event for event in self.events if event.session_id == session_id]
        if after_event_id is None:
            return rows
        position = next(
            (index for index, event in enumerate(rows) if event.event_id == after_event_id),
            -1,
        )
        return rows[position + 1 :]


class FakeChat:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        request: ChatRequest,
        *,
        principal_id: str,
        state_version: int,
    ) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            session_id=request.session_id,
            request_id=request.request_id or "req_stream_00000001",
            trace_id="tr_stream_00000001",
            status="completed",
            answer="已完成。",
            state_version=state_version + 1,
            route="general",
        )


class FakeServices:
    def __init__(self, factory: Any, chat: FakeChat) -> None:
        self.factory = factory
        self.chat = chat


def event_ids(body: str) -> list[str]:
    return [line.removeprefix("id: ") for line in body.splitlines() if line.startswith("id: ")]


@pytest.mark.asyncio
async def test_stream_has_one_terminal_event_and_last_event_id_replays_without_rerun() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add(
            User(
                id="usr_stream_0001",
                external_ref="stream-user",
                display_name="SSE 用户",
                masked_phone="138****0001",
                region="华东",
                created_at=NOW,
            )
        )
        database.add(
            ChatSession(
                id="ses_stream_00000001",
                user_id="usr_stream_0001",
                state_version=1,
                graph_version=GRAPH_VERSION,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.commit()

    chat = FakeChat()
    events = MemoryEventStore()
    app = FastAPI()
    app.include_router(router)
    app.state.services = FakeServices(factory, chat)
    app.state.event_store = events
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/demo-login",
            json={"principal_id": "usr_stream_0001", "role": "customer"},
        )
        token = login.json()["access_token"]
        request_body = {
            "session_id": "ses_stream_00000001",
            "text": "你好",
            "request_id": "req_stream_00000001",
        }
        response = await client.post(
            "/api/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json=request_body,
        )
        ids = event_ids(response.text)
        replay = await client.post(
            "/api/v1/chat/stream",
            headers={
                "Authorization": f"Bearer {token}",
                "Last-Event-ID": ids[0],
            },
            json=request_body,
        )
    await engine.dispose()

    assert response.status_code == 200
    assert response.text.count("event: completed") == 1
    assert "event: error" not in response.text
    assert len(ids) == 3
    assert event_ids(replay.text) == ids[1:]
    assert chat.calls == 1
    assert "reasoning_content" not in response.text
