from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.models import AgentRun, ChatMessage, ChatSession, ToolCallLog

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class StateVersionConflict(RuntimeError):
    pass


def redact_message(value: str) -> str:
    return EMAIL.sub("[EMAIL]", PHONE.sub("[PHONE]", value))


class MySQLTraceSink:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def persist(self, state: dict[str, Any]) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        request_id = str(state["request_id"])
        session_id = str(state["session_id"])
        principal_id = str(state["principal_id"])
        expected_version = int(state.get("state_version", 0))
        async with self.factory() as session:
            existing_run = await session.scalar(
                select(AgentRun).where(AgentRun.request_id == request_id)
            )
            if existing_run is not None:
                if existing_run.session_id != session_id or existing_run.user_id != principal_id:
                    raise StateVersionConflict("request_id is already bound to another session")
                return

            chat = await session.get(ChatSession, session_id)
            if chat is None:
                if expected_version != 0:
                    raise StateVersionConflict("new session must start at state_version 0")
                chat = ChatSession(
                    id=session_id,
                    user_id=principal_id,
                    state_version=1,
                    graph_version=str(state["graph_version"]),
                    created_at=now,
                    updated_at=now,
                )
                session.add(chat)
                await session.flush()
            else:
                if chat.graph_version != state["graph_version"]:
                    raise StateVersionConflict("checkpoint graph version is incompatible")
                result = await session.execute(
                    update(ChatSession)
                    .where(
                        ChatSession.id == session_id,
                        ChatSession.user_id == principal_id,
                        ChatSession.state_version == expected_version,
                    )
                    .values(state_version=expected_version + 1, updated_at=now)
                )
                if cast(CursorResult[Any], result).rowcount != 1:
                    raise StateVersionConflict("chat session state_version conflict")

            run_id = f"run_{uuid4().hex}"
            run = AgentRun(
                id=run_id,
                session_id=session_id,
                user_id=principal_id,
                request_id=request_id,
                trace_id=str(state["trace_id"]),
                graph_version=str(state["graph_version"]),
                prompt_version="p6-v1",
                tool_schema_version="1.0",
                rule_version="business-v1",
                status=str(state.get("status", "completed")),
                decision_summary={
                    "intent": state.get("intent"),
                    "route": state.get("route"),
                    "route_source": state.get("route_source"),
                    "task_count": len(state.get("task_results", [])),
                    "node_count": state.get("node_count", 0),
                    "llm_call_count": state.get("llm_call_count", 0),
                    "tool_call_count": state.get("tool_call_count", 0),
                },
                started_at=now,
                completed_at=now,
            )
            session.add(run)
            await session.flush()
            session.add(
                ChatMessage(
                    id=f"msg_{uuid4().hex}",
                    session_id=session_id,
                    role="user",
                    content_redacted=redact_message(str(state.get("input_text", ""))),
                    created_at=now,
                )
            )
            session.add(
                ChatMessage(
                    id=f"msg_{uuid4().hex}",
                    session_id=session_id,
                    role="assistant",
                    content_redacted=redact_message(str(state.get("final_answer", ""))),
                    created_at=now,
                )
            )
            for call in state.get("tool_calls", []):
                session.add(
                    ToolCallLog(
                        id=f"tcl_{uuid4().hex}",
                        run_id=run_id,
                        tool_call_id=f"tc_{uuid4().hex}",
                        tool_name=str(call.get("tool_name", "unknown")),
                        arguments_summary={"fingerprint": call.get("fingerprint")},
                        result_summary=dict(call.get("result_summary", {})),
                        error_code=call.get("error_code"),
                        duration_ms=int(call.get("duration_ms", 0)),
                        retry_count=0,
                        created_at=now,
                    )
                )
            await session.commit()
