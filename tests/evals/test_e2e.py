from __future__ import annotations

from collections import Counter

import pytest

from evals.e2e.build_suite import CATEGORY_COUNTS, build_cases
from evals.e2e.run import execute_case


def test_e2e_suite_is_deterministic_balanced_and_multi_turn() -> None:
    first = build_cases()
    second = build_cases()
    assert first == second
    assert len(first) == 360
    assert Counter(case["category"] for case in first) == Counter(CATEGORY_COUNTS)
    assert all(len(case["turns"]) >= 2 for case in first)
    abnormal = sum(
        case["category"] in {"missing_information", "tool_failure"} for case in first
    )
    assert abnormal >= 72


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category",
    [
        "knowledge",
        "shopping",
        "order",
        "after_sales",
        "multi_intent",
        "human_general_safety",
        "missing_information",
        "tool_failure",
        "validation_guard",
    ],
)
async def test_each_e2e_category_executes_graph_contract(category: str) -> None:
    case = next(item for item in build_cases() if item["category"] == category)
    result = await execute_case(case)
    assert result["passed"], result
