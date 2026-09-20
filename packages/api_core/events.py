from __future__ import annotations

import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from pydantic import Field
from redis.asyncio import Redis

from packages.contracts.base import StrictModel

FORBIDDEN_KEYS = {
    "authorization",
    "api_key",
    "jwt",
    "prompt",
    "reasoning",
    "reasoning_content",
    "secret",
    "token",
}


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_payload(item)
            for key, item in value.items()
            if str(key).lower() not in FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


class SseEvent(StrictModel):
    event_id: str
    session_id: str
    run_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime
    type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    payload: dict[str, Any]


class EventStore(Protocol):
    async def append(
        self,
        *,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> SseEvent: ...

    async def history(
        self, session_id: str, *, after_event_id: str | None = None
    ) -> list[SseEvent]: ...


class RedisEventStore:
    def __init__(self, redis: Redis, *, retention_seconds: int = 600) -> None:
        self.redis = redis
        self.retention_seconds = retention_seconds

    @staticmethod
    def _events_key(session_id: str) -> str:
        return f"commerce:sse:{session_id}:events"

    @staticmethod
    def _sequence_key(session_id: str) -> str:
        return f"commerce:sse:{session_id}:sequence"

    async def append(
        self,
        *,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> SseEvent:
        sequence_value = self.redis.incr(self._sequence_key(session_id))
        sequence = int(await cast(Awaitable[int], sequence_value))
        event = SseEvent(
            event_id=f"evt_{session_id}_{sequence:08d}",
            session_id=session_id,
            run_id=run_id,
            sequence=sequence,
            timestamp=datetime.now(UTC),
            type=event_type,
            payload=sanitize_payload(payload),
        )
        raw = event.model_dump_json()
        key = self._events_key(session_id)
        await cast(Awaitable[int], self.redis.rpush(key, raw))
        await cast(Awaitable[bool], self.redis.expire(key, self.retention_seconds))
        await cast(
            Awaitable[bool],
            self.redis.expire(self._sequence_key(session_id), self.retention_seconds),
        )
        return event

    async def history(
        self, session_id: str, *, after_event_id: str | None = None
    ) -> list[SseEvent]:
        rows_value = self.redis.lrange(self._events_key(session_id), 0, -1)
        rows = await cast(Awaitable[list[Any]], rows_value)
        events = [
            SseEvent.model_validate_json(row.decode() if isinstance(row, bytes) else row)
            for row in rows
        ]
        if after_event_id is None:
            return events
        for index, event in enumerate(events):
            if event.event_id == after_event_id:
                return events[index + 1 :]
        return events


def encode_sse(event: SseEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"
