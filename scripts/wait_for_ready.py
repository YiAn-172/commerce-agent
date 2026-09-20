from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_ENDPOINTS = {
    "api": "http://api:8000/health/ready",
    "intent-service": "http://intent-service:8001/health/ready",
    "mcp-catalog": "http://mcp-catalog:8101/health/ready",
    "mcp-order": "http://mcp-order:8102/health/ready",
    "mcp-after-sales": "http://mcp-after-sales:8103/health/ready",
    "web": "http://web:3000/api/health",
    "elasticsearch": "http://elasticsearch:9200/_cluster/health",
    "milvus": "http://milvus:9091/healthz",
}


def parse_endpoint(value: str) -> tuple[str, str]:
    name, separator, url = value.partition("=")
    if not separator or not name or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("endpoint must use NAME=http(s)://URL")
    return name, url


async def probe(client: httpx.AsyncClient, name: str, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await client.get(url)
        return {
            "name": name,
            "url": url,
            "ready": response.is_success,
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "body_preview": response.text[:300],
        }
    except httpx.HTTPError as error:
        return {
            "name": name,
            "url": url,
            "ready": False,
            "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(error),
        }


async def wait_until_ready(
    endpoints: dict[str, str], timeout: float, interval: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts = 0
    latest: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        while True:
            attempts += 1
            latest = list(
                await asyncio.gather(*(probe(client, name, url) for name, url in endpoints.items()))
            )
            if all(bool(item["ready"]) for item in latest):
                status = "verified"
                break
            if time.monotonic() >= deadline:
                status = "failed"
                break
            await asyncio.sleep(interval)
    return {
        "schema_version": "1.0",
        "evaluation_status": status,
        "timeout_seconds": timeout,
        "attempts": attempts,
        "services": latest,
        "created_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for the complete demo stack")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument(
        "--endpoint",
        action="append",
        type=parse_endpoint,
        default=[],
        help="replace defaults with repeatable NAME=http(s)://URL entries",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/release/readiness.json"))
    args = parser.parse_args()
    endpoints = dict(args.endpoint) if args.endpoint else DEFAULT_ENDPOINTS
    report = asyncio.run(wait_until_ready(endpoints, args.timeout, args.interval))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["evaluation_status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
