from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REVIEWED = {"reviewed"}
VERDICTS = {"pass", "fail"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_review(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("review_status") not in REVIEWED:
        return errors
    if not str(row.get("reviewer_id") or "").strip():
        errors.append("reviewer_id is required")
    reviewed_at = str(row.get("reviewed_at") or "").strip()
    if not reviewed_at:
        errors.append("reviewed_at is required")
    else:
        try:
            parsed_reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("reviewed_at must be a valid ISO 8601 timestamp")
        else:
            if parsed_reviewed_at.utcoffset() is None:
                errors.append("reviewed_at must include a timezone")
    for field in ("relevance_score", "clarity_score"):
        value = row.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            errors.append(f"{field} must be an integer from 1 to 5")
    if not isinstance(row.get("safe_and_helpful"), bool):
        errors.append("safe_and_helpful must be boolean")
    if row.get("verdict") not in VERDICTS:
        errors.append("verdict must be pass or fail")
    return errors


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate_ids = [
        case_id
        for case_id, count in Counter(str(row.get("case_id")) for row in rows).items()
        if count > 1
    ]
    invalid: list[dict[str, Any]] = []
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        errors = validate_review(row)
        if errors:
            invalid.append({"case_id": row.get("case_id"), "errors": errors})
        if row.get("review_status") in REVIEWED and not errors:
            reviewed.append(row)
    pending = len(rows) - len(reviewed)
    passed = sum(row.get("verdict") == "pass" for row in reviewed)
    failed = sum(row.get("verdict") == "fail" for row in reviewed)
    relevance = [int(row["relevance_score"]) for row in reviewed]
    clarity = [int(row["clarity_score"]) for row in reviewed]
    complete = bool(rows) and not duplicate_ids and not invalid and pending == 0
    quality_gate_passed = complete and failed == 0
    return {
        "required_sample_rate": 0.2,
        "sample_count": len(rows),
        "status": "human_review_verified" if complete else "pending_human_review",
        "quality_gate_passed": quality_gate_passed,
        "reviewed": len(reviewed),
        "pending": pending,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(reviewed) if reviewed else None,
        "mean_relevance_score": sum(relevance) / len(relevance) if relevance else None,
        "mean_clarity_score": sum(clarity) / len(clarity) if clarity else None,
        "duplicate_case_ids": duplicate_ids,
        "invalid_reviews": invalid,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def update_e2e_report(path: Path, summary: dict[str, Any]) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    report["manual_review"] = summary
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and summarize the 20% human review")
    parser.add_argument(
        "--queue",
        type=Path,
        default=Path("reports/eval/e2e_360_v1_manual_review.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/eval/e2e_360_v1_manual_review_summary.json"),
    )
    parser.add_argument(
        "--e2e-report",
        type=Path,
        default=Path("reports/eval/e2e_360_v1.json"),
    )
    args = parser.parse_args()
    rows = read_jsonl(args.queue)
    if len(rows) != 72:
        raise SystemExit(f"manual review queue must contain exactly 72 rows; found {len(rows)}")
    summary = summarize(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    update_e2e_report(args.e2e_report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "human_review_verified":
        raise SystemExit(2)
    if not summary["quality_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
