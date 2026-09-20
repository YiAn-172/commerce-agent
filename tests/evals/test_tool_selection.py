from __future__ import annotations

from collections import Counter
from typing import Any, cast

import pytest

from evals.tool_selection.build_suite import CATEGORY_COUNTS, build_cases
from evals.tool_selection.run import evaluate
from packages.contracts.intent import (
    HighLevelRoute,
    IntentCandidate,
    IntentLabel,
    IntentPrediction,
)


def test_tool_selection_suite_is_balanced_and_deterministic() -> None:
    first = build_cases()
    second = build_cases()
    assert first == second
    assert len(first) == 1000
    assert Counter(case["category"] for case in first) == Counter(CATEGORY_COUNTS)
    assert len({case["case_id"] for case in first}) == 1000
    assert sum(bool(case["expected_tools"]) for case in first) == 500
    assert sum(not case["expected_tools"] for case in first) == 500


class StubRuntime:
    def predict(self, text: str, previous_user_text: str | None = None) -> IntentPrediction:
        del text, previous_user_text
        return IntentPrediction(
            label=IntentLabel.OUT_OF_SCOPE,
            route=HighLevelRoute.SAFE_REPLY,
            confidence=0.99,
            candidates=[
                IntentCandidate(label=IntentLabel.OUT_OF_SCOPE, probability=0.99),
                IntentCandidate(label=IntentLabel.CHITCHAT, probability=0.01),
            ],
            model_version="stub",
            decision="safe_reply",
            margin=0.98,
        )


@pytest.mark.asyncio
async def test_tool_selection_failure_reason_taxonomy() -> None:
    cases = [
        {
            "case_id": "shopping_001",
            "category": "shopping",
            "text": "unsupported request",
            "scenario": "normal",
            "expected_tools": ["search_products", "check_inventory"],
        }
    ]
    metrics = await evaluate(cases, cast(Any, StubRuntime()))
    assert metrics["failure_reasons"]["abstained_without_tool"] == 1
    assert metrics["failure_reasons"]["wrong_tool_sequence"] == 0
    assert metrics["failure_reasons"]["unexpected_tool_call"] == 0
