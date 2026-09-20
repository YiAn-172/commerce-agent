from __future__ import annotations

from evals.load.common import Sample, latency_summary, percentile
from evals.load.mixed import request_kind


def test_percentile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.95) == 100.0
    assert latency_summary([Sample(value, 200, True) for value in values])["p99_ms"] == 100.0


def test_mixed_workload_distribution_is_exact_per_hundred() -> None:
    counts = {name: sum(request_kind(index) == name for index in range(100)) for name in {
        "health_live",
        "meta",
        "session_list",
        "knowledge_chat",
        "order_chat",
        "safety_chat",
    }}
    assert counts == {
        "health_live": 10,
        "meta": 10,
        "session_list": 20,
        "knowledge_chat": 25,
        "order_chat": 20,
        "safety_chat": 15,
    }
