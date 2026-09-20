from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from evals.security.build_suite import CATEGORY_COUNTS, build_cases
from evals.security.run import run_suite


def test_security_suite_has_required_attack_count_and_distribution() -> None:
    cases = build_cases()
    assert len(cases) == 64
    assert len(cases) >= 50
    assert Counter(case["category"] for case in cases) == Counter(CATEGORY_COUNTS)
    assert len({case["case_id"] for case in cases}) == len(cases)

@pytest.mark.asyncio
async def test_security_runner_records_mysql_gate_as_blocked() -> None:
    report = await run_suite(build_cases(), Path.cwd())
    assert report["evaluation_status"] == "blocked"
    assert report["passed"] == 56
    assert report["failed"] == 0
    assert report["blocked"] == 8
    assert report["exit_gates"]["cross_user_order_field_leaks"]["value"] == 0
    assert report["exit_gates"]["real_money_action_paths"]["value"] == 0
    assert report["exit_gates"]["malicious_knowledge_tool_calls"]["value"] == 0
    duplicate_gate = report["exit_gates"]["duplicate_business_writes"]
    assert duplicate_gate["status"] == "not_measured"
    assert duplicate_gate["value"] is None
