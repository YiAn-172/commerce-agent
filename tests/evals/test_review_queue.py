from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.review_queue import (
    build_queue,
    finalize,
    read_jsonl,
    verify_human_gold_suite,
)


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def routing_rows() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "route_001",
            "category": "knowledge",
            "text": "保修政策",
            "previous_user_text": None,
            "expected_route": "knowledge",
            "expected_decision": "auto_route",
            "provenance": "template",
        },
        {
            "case_id": "route_002",
            "category": "ambiguous",
            "text": "这个怎么样",
            "previous_user_text": None,
            "expected_route": None,
            "expected_decision": "clarify",
            "provenance": "template",
        },
    ]


def test_build_queue_preserves_review_only_when_source_row_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    queue = tmp_path / "queue.jsonl"
    rows = routing_rows()
    write_rows(source, rows)
    build_queue(source, queue, "routing")
    review_rows = read_jsonl(queue)
    review_rows[0].update(
        {
            "review_status": "reviewed",
            "reviewer_id": "reviewer",
            "reviewed_at": "2026-09-18T10:00:00Z",
            "verdict": "accept",
        }
    )
    write_rows(queue, review_rows)

    result = build_queue(source, queue, "routing")
    assert result["preserved_reviewed_rows"] == 1
    assert read_jsonl(queue)[0]["review_status"] == "reviewed"

    rows[0]["text"] = "更新后的保修政策问题"
    write_rows(source, rows)
    build_queue(source, queue, "routing")
    assert read_jsonl(queue)[0]["review_status"] == "pending_human_review"


def test_finalize_fails_closed_until_every_row_is_reviewed(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "gold.jsonl"
    summary = tmp_path / "summary.json"
    write_rows(source, routing_rows())
    build_queue(source, queue, "routing")

    result = finalize(
        source_path=source,
        queue_path=queue,
        output_path=output,
        summary_path=summary,
        kind="routing",
        expected_rows=2,
    )
    assert result["status"] == "pending_human_review"
    assert result["reviewed"] == 0
    assert not output.exists()


def test_finalize_writes_human_adjudicated_suite(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "gold.jsonl"
    summary = tmp_path / "summary.json"
    write_rows(source, routing_rows())
    build_queue(source, queue, "routing")
    reviews = read_jsonl(queue)
    for row in reviews:
        row.update(
            {
                "review_status": "reviewed",
                "reviewer_id": "reviewer",
                "reviewed_at": "2026-09-18T10:00:00Z",
                "verdict": "accept",
            }
        )
    reviews[1].update(
        {
            "verdict": "correct",
            "adjudicated_route": "safe_reply",
            "adjudicated_decision": "safe_reply",
        }
    )
    write_rows(queue, reviews)

    result = finalize(
        source_path=source,
        queue_path=queue,
        output_path=output,
        summary_path=summary,
        kind="routing",
        expected_rows=2,
    )
    gold = read_jsonl(output)
    assert result["status"] == "human_gold_verified"
    assert result["adjudicated_sha256"]
    assert gold[0]["expected_route"] == "knowledge"
    assert gold[1]["expected_route"] == "safe_reply"
    assert all(row["provenance"] == "independent_human_adjudication" for row in gold)


def test_finalize_rejects_invalid_review_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "gold.jsonl"
    summary = tmp_path / "summary.json"
    write_rows(source, routing_rows())
    build_queue(source, queue, "routing")
    reviews = read_jsonl(queue)
    for row in reviews:
        row.update(
            {
                "review_status": "reviewed",
                "reviewer_id": "reviewer",
                "reviewed_at": "not-a-timestamp",
                "verdict": "accept",
            }
        )
    write_rows(queue, reviews)

    result = finalize(
        source_path=source,
        queue_path=queue,
        output_path=output,
        summary_path=summary,
        kind="routing",
        expected_rows=2,
    )

    assert result["status"] == "pending_human_review"
    assert result["reviewed"] == 0
    assert all(
        "reviewed_at must be a valid ISO 8601 timestamp" in item["errors"]
        for item in result["invalid_reviews"]
    )
    assert not output.exists()


def test_human_gold_verification_detects_source_or_output_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "gold.jsonl"
    summary = tmp_path / "summary.json"
    write_rows(source, routing_rows())
    build_queue(source, queue, "routing")
    reviews = read_jsonl(queue)
    for row in reviews:
        row.update(
            {
                "review_status": "reviewed",
                "reviewer_id": "reviewer",
                "reviewed_at": "2026-09-18T10:00:00Z",
                "verdict": "accept",
            }
        )
    write_rows(queue, reviews)
    finalize(
        source_path=source,
        queue_path=queue,
        output_path=output,
        summary_path=summary,
        kind="routing",
        expected_rows=2,
    )

    verified = verify_human_gold_suite(
        source_path=source,
        adjudicated_path=output,
        summary_path=summary,
        kind="routing",
        expected_rows=2,
    )
    assert verified["status"] == "human_gold_verified"

    output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    try:
        verify_human_gold_suite(
            source_path=source,
            adjudicated_path=output,
            summary_path=summary,
            kind="routing",
            expected_rows=2,
        )
    except ValueError as error:
        assert "adjudicated_sha256" in str(error)
    else:
        raise AssertionError("tampered adjudicated suite was accepted")
