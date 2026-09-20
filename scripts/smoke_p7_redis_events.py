from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from redis.asyncio import Redis

from apps.api.settings import Settings
from packages.api_core.events import RedisEventStore


async def main_async() -> None:
    settings = Settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    session_id = f"ses_sse_{uuid4().hex[:16]}"
    run_id = f"run_sse_{uuid4().hex[:16]}"
    store = RedisEventStore(redis, retention_seconds=settings.sse_retention_seconds)
    try:
        first = await store.append(
            session_id=session_id,
            run_id=run_id,
            event_type="run_started",
            payload={"token": "must-not-be-stored", "request_id": "req_sse_smoke_0001"},
        )
        await store.append(
            session_id=session_id,
            run_id=run_id,
            event_type="message_completed",
            payload={"answer": "完成", "reasoning_content": "must-not-be-stored"},
        )
        await store.append(
            session_id=session_id,
            run_id=run_id,
            event_type="completed",
            payload={"status": "completed"},
        )
        all_events = await store.history(session_id)
        resumed = await store.history(session_id, after_event_id=first.event_id)
        ttl = int(await redis.ttl(store._events_key(session_id)))
        serialized = json.dumps(
            [item.model_dump(mode="json") for item in all_events],
            ensure_ascii=False,
        )
        if len(all_events) != 3 or len(resumed) != 2:
            raise RuntimeError("Redis SSE history or Last-Event-ID resume failed")
        if "must-not-be-stored" in serialized:
            raise RuntimeError("Redis SSE payload retained a forbidden secret or reasoning field")
        if not 0 < ttl <= settings.sse_retention_seconds:
            raise RuntimeError(f"unexpected SSE retention TTL: {ttl}")
        report = {
            "status": "passed",
            "event_count": len(all_events),
            "resume_count": len(resumed),
            "sequences": [item.sequence for item in all_events],
            "terminal_events": [
                item.type for item in all_events if item.type in {"completed", "error"}
            ],
            "ttl_seconds": ttl,
            "forbidden_payload_removed": True,
        }
        output = Path("reports/api/p7_sse_redis.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await redis.aclose()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
