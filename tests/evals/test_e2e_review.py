from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from apps.api.settings import Settings
from evals.e2e.judge import judge_items, run_judge, update_e2e_report
from evals.e2e.manual_review import summarize
from evals.e2e.run import write_manual_queue


def pending_row(index: int) -> dict[str, object]:
    return {
        "case_id": f"case_{index:03d}",
        "review_status": "pending_human_review",
        "reviewer_id": None,
        "reviewed_at": None,
        "relevance_score": None,
        "clarity_score": None,
        "safe_and_helpful": None,
        "verdict": None,
    }


def test_manual_review_summary_stays_pending_until_all_rows_are_reviewed() -> None:
    rows = [pending_row(index) for index in range(72)]
    summary = summarize(rows)
    assert summary["status"] == "pending_human_review"
    assert summary["reviewed"] == 0
    assert summary["pending"] == 72
    assert summary["quality_gate_passed"] is False


def test_manual_review_summary_verifies_complete_reviews() -> None:
    rows = [pending_row(index) for index in range(72)]
    for row in rows:
        row.update(
            {
                "review_status": "reviewed",
                "reviewer_id": "reviewer_demo",
                "reviewed_at": "2026-09-18T10:00:00Z",
                "relevance_score": 5,
                "clarity_score": 4,
                "safe_and_helpful": True,
                "verdict": "pass",
            }
        )
    summary = summarize(rows)
    assert summary["status"] == "human_review_verified"
    assert summary["quality_gate_passed"] is True
    assert summary["pass_rate"] == 1.0


def test_manual_review_rejects_invalid_or_timezone_naive_timestamp() -> None:
    rows = [pending_row(index) for index in range(2)]
    for row, timestamp in zip(
        rows,
        ("not-a-timestamp", "2026-09-18T10:00:00"),
        strict=True,
    ):
        row.update(
            {
                "review_status": "reviewed",
                "reviewer_id": "reviewer_demo",
                "reviewed_at": timestamp,
                "relevance_score": 5,
                "clarity_score": 4,
                "safe_and_helpful": True,
                "verdict": "pass",
            }
        )
    summary = summarize(rows)
    assert summary["status"] == "pending_human_review"
    assert summary["reviewed"] == 0
    assert len(summary["invalid_reviews"]) == 2


def test_judge_items_use_target_turn_only() -> None:
    case = {
        "case_id": "case_001",
        "category": "knowledge",
        "turns": [
            {
                "expected": {"text": "你好", "expected_status": "completed"},
                "actual": {"answer": "你好"},
            },
            {
                "expected": {"text": "保修政策", "expected_status": "completed"},
                "actual": {"answer": "根据政策可以申请。"},
            },
        ],
    }
    report = {"cases": [deepcopy(case) for _ in range(360)]}
    items = judge_items(report)
    assert len(items) == 360
    assert items[0]["user_text"] == "保修政策"
    assert items[0]["assistant_answer"] == "根据政策可以申请。"


@pytest.mark.asyncio
async def test_judge_fails_closed_without_api_key() -> None:
    case = {
        "case_id": "case_001",
        "category": "knowledge",
        "turns": [
            {
                "expected": {"text": "保修政策", "expected_status": "completed"},
                "actual": {"answer": "根据政策可以申请。"},
            }
        ],
    }
    report = {"cases": [deepcopy(case) for _ in range(360)]}
    settings = Settings(app_secret="judge-test-secret", deepseek_api_key=None)
    result = await run_judge(report, settings=settings)
    assert result["status"] == "not_run"
    assert result["judged"] == 0


def test_verified_judge_keeps_e2e_pending_until_human_review(tmp_path: Path) -> None:
    report_path = tmp_path / "e2e.json"
    report_path.write_text(
        json.dumps(
            {
                "evaluation_status": "deterministic_only",
                "manual_review": {"status": "pending_human_review"},
            }
        ),
        encoding="utf-8",
    )

    update_e2e_report(report_path, {"status": "verified", "judged": 360})

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["evaluation_status"] == "judge_verified_manual_review_pending"
    assert report["judge"]["status"] == "verified"

def test_manual_queue_preserves_review_only_for_unchanged_content(tmp_path: Path) -> None:
    queue = tmp_path / "manual.jsonl"
    row = {
        "case_id": "case_001",
        "category": "knowledge",
        "turns": [{"user_text": "问题", "assistant_answer": "回答"}],
        "review_status": "pending_human_review",
        "reviewer_id": None,
        "reviewed_at": None,
        "relevance_score": None,
        "clarity_score": None,
        "safe_and_helpful": None,
        "verdict": None,
        "notes": None,
    }
    report: dict[str, Any] = {"manual_review": {"queue": [row]}}
    write_manual_queue(report, queue)
    saved = json.loads(queue.read_text(encoding="utf-8"))
    saved.update(
        {
            "review_status": "reviewed",
            "reviewer_id": "reviewer",
            "reviewed_at": "2026-09-18T10:00:00Z",
            "relevance_score": 5,
            "clarity_score": 5,
            "safe_and_helpful": True,
            "verdict": "pass",
        }
    )
    queue.write_text(json.dumps(saved, ensure_ascii=False) + "\n", encoding="utf-8")

    write_manual_queue(report, queue)
    preserved = json.loads(queue.read_text(encoding="utf-8"))
    assert preserved["review_status"] == "reviewed"

    changed = deepcopy(report)
    changed["manual_review"]["queue"][0]["turns"][0]["assistant_answer"] = "新回答"
    write_manual_queue(changed, queue)
    invalidated = json.loads(queue.read_text(encoding="utf-8"))
    assert invalidated["review_status"] == "pending_human_review"
    assert invalidated["reviewer_id"] is None
