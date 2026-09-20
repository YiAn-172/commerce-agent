from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from openai import APIStatusError

from apps.api.settings import Settings
from packages.agent_core.contracts import RewriteOutput
from packages.agent_core.deepseek import DeepSeekAdapter


async def main_async() -> None:
    settings = Settings()
    if settings.deepseek_api_key is None:
        raise SystemExit("DEEPSEEK_API_KEY is not configured")

    adapter = DeepSeekAdapter(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
        max_retries=settings.deepseek_max_retries,
        temperature=settings.deepseek_temperature,
    )
    started = perf_counter()
    try:
        result = await adapter.generate(
            "knowledge_rewrite_v1",
            {"query": "七天无理由退货需要满足什么条件？", "intent": "return_policy"},
            RewriteOutput,
        )
    except APIStatusError as error:
        report = {
            "status": "blocked_external",
            "checked_at": datetime.now(UTC).isoformat(),
            "model": settings.deepseek_model,
            "base_url": settings.deepseek_base_url,
            "prompt_id": "knowledge_rewrite_v1",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "http_status": error.status_code,
            "error_type": type(error).__name__,
            "error_summary": "Insufficient Balance"
            if error.status_code == 402
            else "DeepSeek API request rejected",
        }
        write_report(report)
        raise SystemExit(2) from None
    report = {
        "status": "passed",
        "checked_at": datetime.now(UTC).isoformat(),
        "model": settings.deepseek_model,
        "base_url": settings.deepseek_base_url,
        "prompt_id": "knowledge_rewrite_v1",
        "latency_ms": round((perf_counter() - started) * 1000, 2),
        "validated_output": result.model_dump(),
    }
    write_report(report)


def write_report(report: dict[str, object]) -> None:
    output = Path("reports/agent/p6_deepseek_live.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
