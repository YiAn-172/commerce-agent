from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

RequestFactory = Callable[[httpx.AsyncClient, int], Awaitable[httpx.Response]]


@dataclass(frozen=True)
class Sample:
    latency_ms: float
    status_code: int | None
    ok: bool
    error: str | None = None
    cache_status: str | None = None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[rank], 3)


def latency_summary(samples: list[Sample]) -> dict[str, Any]:
    values = [sample.latency_ms for sample in samples]
    return {
        "count": len(values),
        "min_ms": round(min(values), 3) if values else None,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "max_ms": round(max(values), 3) if values else None,
    }


def physical_memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError):
            return None
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return None
    try:
        return int(sysconf("SC_PAGE_SIZE") * sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError):
        return None


def environment(base_url: str) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "operating_system": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "ram_bytes": physical_memory_bytes(),
        "gpu": os.getenv("LOAD_TEST_GPU", "not_detected_or_not_reported"),
        "network": {
            "base_url": base_url,
            "topology": "caller_to_configured_base_url",
            "bandwidth": "not_measured",
        },
        "deepseek_model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    }


async def preflight(client: httpx.AsyncClient) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}
    for endpoint in ("/health/live", "/health/ready"):
        try:
            response = await client.get(endpoint)
            checks[endpoint] = {"status_code": response.status_code, "ok": response.is_success}
        except httpx.HTTPError as error:
            checks[endpoint] = {"status_code": None, "ok": False, "error": str(error)}
    return all(bool(item["ok"]) for item in checks.values()), checks


async def customer_token(client: httpx.AsyncClient, principal_id: str = "usr_demo_0001") -> str:
    response = await client.post(
        "/api/v1/auth/demo-login",
        json={"principal_id": principal_id, "role": "customer"},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def create_sessions(
    client: httpx.AsyncClient,
    *,
    token: str,
    count: int,
    prefix: str,
) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"}
    sessions: list[str] = []
    stamp = int(time.time() * 1000)
    for index in range(count):
        session_id = f"ses_{prefix}_{stamp}_{index:03d}"
        response = await client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"session_id": session_id},
        )
        response.raise_for_status()
        sessions.append(session_id)
    return sessions


async def run_requests(
    client: httpx.AsyncClient,
    *,
    total: int,
    concurrency: int,
    request_factory: RequestFactory,
) -> tuple[list[Sample], float]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index: int) -> Sample:
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await request_factory(client, index)
                latency = (time.perf_counter() - started) * 1000
                cache_status = response.headers.get("x-cache") or response.headers.get(
                    "x-cache-status"
                )
                return Sample(
                    latency_ms=latency,
                    status_code=response.status_code,
                    ok=response.is_success,
                    error=None if response.is_success else response.text[:500],
                    cache_status=cache_status,
                )
            except httpx.HTTPError as error:
                return Sample(
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status_code=None,
                    ok=False,
                    error=str(error),
                )

    started = time.perf_counter()
    samples = await asyncio.gather(*(one(index) for index in range(total)))
    return samples, time.perf_counter() - started


def measured_report(
    *,
    suite: str,
    base_url: str,
    requested: int,
    concurrency: int,
    samples: list[Sample],
    elapsed_seconds: float,
    preflight_checks: dict[str, Any],
    workload: dict[str, Any],
    cache: dict[str, Any],
) -> dict[str, Any]:
    successes = sum(sample.ok for sample in samples)
    status_codes = Counter(
        str(sample.status_code) if sample.status_code is not None else "transport_error"
        for sample in samples
    )
    errors = [sample.error for sample in samples if sample.error][:20]
    return {
        "schema_version": "1.0",
        "suite": suite,
        "evaluation_status": "verified" if successes == len(samples) else "measured_with_errors",
        "measurement_scope": "live_http_end_to_end",
        "requested": requested,
        "completed": len(samples),
        "concurrency": concurrency,
        "test_time_seconds": round(elapsed_seconds, 3),
        "throughput_requests_per_second": round(len(samples) / elapsed_seconds, 3),
        "successes": successes,
        "errors": len(samples) - successes,
        "error_rate": (len(samples) - successes) / len(samples),
        "status_codes": dict(status_codes),
        "latency": latency_summary(samples),
        "external_deepseek_latency": {
            "status": "not_observable_from_api_response",
            "reason": "API responses do not expose trusted component timing fields.",
        },
        "local_service_latency": {
            "status": "included_in_end_to_end_only",
            "reason": "No trusted server-timing breakdown is currently emitted.",
        },
        "cache": cache,
        "workload": workload,
        "preflight": preflight_checks,
        "environment": environment(base_url),
        "sample_errors": errors,
        "created_at": datetime.now(UTC).isoformat(),
    }


def blocked_report(
    *,
    suite: str,
    base_url: str,
    requested: int,
    concurrency: int,
    preflight_checks: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "suite": suite,
        "evaluation_status": "not_run",
        "measurement_scope": "live_http_end_to_end",
        "requested": requested,
        "completed": 0,
        "concurrency": concurrency,
        "reason": reason,
        "preflight": preflight_checks,
        "environment": environment(base_url),
        "metrics": "not_measured",
        "created_at": datetime.now(UTC).isoformat(),
    }


def write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
