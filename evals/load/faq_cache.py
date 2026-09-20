from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from evals.load.common import (
    Sample,
    blocked_report,
    create_sessions,
    customer_token,
    latency_summary,
    measured_report,
    preflight,
    run_requests,
    write_report,
)

SUITE = "load_faq_cache"
FAQ_TEXT = "手机保修政策是什么"


def cache_counts(samples: list[Sample]) -> dict[str, int]:
    return dict(Counter(sample.cache_status or "header_absent" for sample in samples))


async def execute(*, base_url: str, requests: int, timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False
    ) as client:
        ready, checks = await preflight(client)
        if not ready:
            return blocked_report(
                suite=SUITE,
                base_url=base_url,
                requested=requests,
                concurrency=1,
                preflight_checks=checks,
                reason="API live/ready preflight failed; no cache-load metrics were measured.",
            )
        try:
            token = await customer_token(client)
            sessions = await create_sessions(client, token=token, count=1, prefix="loadfaq")
        except httpx.HTTPError as error:
            return blocked_report(
                suite=SUITE,
                base_url=base_url,
                requested=requests,
                concurrency=1,
                preflight_checks=checks,
                reason=f"authenticated cache-load setup failed: {error}",
            )
        headers = {"Authorization": f"Bearer {token}"}
        run_suffix = uuid4().hex[:12]

        async def request_factory(client: httpx.AsyncClient, index: int) -> httpx.Response:
            return await client.post(
                "/api/v1/chat",
                headers=headers,
                json={
                    "session_id": sessions[0],
                    "text": FAQ_TEXT,
                    "request_id": f"req_loadfaq_{run_suffix}_{index:08d}",
                },
            )

        cold, cold_elapsed = await run_requests(
            client, total=1, concurrency=1, request_factory=request_factory
        )
        hot_total = max(requests - 1, 0)
        hot, hot_elapsed = await run_requests(
            client,
            total=hot_total,
            concurrency=1,
            request_factory=request_factory,
        )
        samples = cold + hot
        hot_hits = sum(sample.cache_status == "HIT" for sample in hot)
        report = measured_report(
            suite=SUITE,
            base_url=base_url,
            requested=requests,
            concurrency=1,
            samples=samples,
            elapsed_seconds=cold_elapsed + hot_elapsed,
            preflight_checks=checks,
            workload={"endpoint": "/api/v1/chat", "text": FAQ_TEXT, "sequential": True},
            cache={
                "cold": {"requests": len(cold), "latency": latency_summary(cold)},
                "hot": {"requests": len(hot), "latency": latency_summary(hot)},
                "observed_response_headers": cache_counts(samples),
                "hot_hits": hot_hits,
                "hot_hit_rate": hot_hits / len(hot) if hot else None,
                "verification": (
                    "header_verified" if hot and hot_hits > 0 else "no_hot_cache_hit_observed"
                ),
            },
        )
        if report["cache"]["verification"] != "header_verified":
            report["evaluation_status"] = "measured_cache_effect_unverified"
            report["cache"]["reason"] = (
                "Cold/hot latency was measured, but the API did not expose a trusted "
                "cache-hit header."
            )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the live FAQ cold/hot cache load suite")
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, default=Path("reports/eval/load_faq_cache.json"))
    args = parser.parse_args()
    report = asyncio.run(
        execute(base_url=args.base_url, requests=args.requests, timeout=args.timeout)
    )
    write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["evaluation_status"] == "not_run":
        raise SystemExit(2)
    if report["evaluation_status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
