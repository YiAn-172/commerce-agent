from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ReviewKind = Literal["routing", "tool_selection"]
ROUTES = {"knowledge", "shopping", "order", "after_sales", "human", "general", "safe_reply"}
DECISIONS = {"auto_route", "clarify", "safe_reply"}
TOOLS = {
    "search_products",
    "check_inventory",
    "get_order_detail",
    "list_recent_orders",
    "track_logistics",
    "check_after_sales_eligibility",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def row_sha256(row: dict[str, Any]) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_review_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"review summary is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"review summary must be a JSON object: {path}")
    return value


def verify_human_gold_suite(
    *,
    source_path: Path,
    adjudicated_path: Path,
    summary_path: Path,
    kind: ReviewKind,
    expected_rows: int,
) -> dict[str, Any]:
    if not source_path.exists():
        raise ValueError(f"source suite is missing: {source_path}")
    if not adjudicated_path.exists():
        raise ValueError(f"adjudicated suite is missing: {adjudicated_path}")
    summary = read_review_summary(summary_path)
    checks = {
        "status": summary.get("status") == "human_gold_verified",
        "kind": summary.get("kind") == kind,
        "expected_rows": summary.get("expected_rows") == expected_rows,
        "source_sha256": summary.get("source_sha256") == file_sha256(source_path),
        "adjudicated_sha256": summary.get("adjudicated_sha256") == file_sha256(adjudicated_path),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(
            f"human-gold verification failed for {adjudicated_path}: " + ", ".join(failed)
        )
    rows = read_jsonl(adjudicated_path)
    if len(rows) != expected_rows:
        raise ValueError(f"human-gold suite must contain {expected_rows} rows; found {len(rows)}")
    if any(row.get("provenance") != "independent_human_adjudication" for row in rows):
        raise ValueError("human-gold suite contains non-adjudicated provenance")
    return summary


def verify_ai_assisted_suite(
    *,
    source_path: Path,
    adjudicated_path: Path,
    summary_path: Path,
    kind: ReviewKind,
    expected_rows: int,
) -> dict[str, Any]:
    if not source_path.exists() or not adjudicated_path.exists() or not summary_path.exists():
        raise ValueError("AI-assisted suite evidence is incomplete")
    summary = read_review_summary(summary_path)
    checks = {
        "status": summary.get("status") == "ai_assisted_verified_for_demo",
        "kind": summary.get("kind") == kind,
        "expected_rows": summary.get("expected_rows") == expected_rows,
        "source_sha256": summary.get("source_sha256") == file_sha256(source_path),
        "adjudicated_sha256": summary.get("adjudicated_sha256") == file_sha256(adjudicated_path),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("AI-assisted suite verification failed: " + ", ".join(failed))
    rows = read_jsonl(adjudicated_path)
    if len(rows) != expected_rows:
        raise ValueError(f"AI-assisted suite must contain {expected_rows} rows")
    if any(row.get("provenance") != "ai_assisted_project_owner_accepted" for row in rows):
        raise ValueError("AI-assisted suite contains invalid provenance")
    return summary


def queue_row(source: dict[str, Any], kind: ReviewKind) -> dict[str, Any]:
    common: dict[str, Any] = {
        "case_id": source["case_id"],
        "category": source["category"],
        "text": source["text"],
        "previous_user_text": source.get("previous_user_text"),
        "source_row_sha256": row_sha256(source),
        "review_status": "pending_human_review",
        "reviewer_id": None,
        "reviewed_at": None,
        "verdict": None,
        "notes": None,
    }
    if kind == "routing":
        common.update(
            {
                "proposed_route": source["expected_route"],
                "proposed_decision": source["expected_decision"],
                "adjudicated_route": None,
                "adjudicated_decision": None,
            }
        )
    else:
        common.update(
            {
                "proposed_tools": source["expected_tools"],
                "adjudicated_tools": None,
            }
        )
    return common


def build_queue(source_path: Path, output_path: Path, kind: ReviewKind) -> dict[str, Any]:
    source_rows = read_jsonl(source_path)
    existing: dict[str, dict[str, Any]] = {}
    if output_path.exists():
        existing = {str(row["case_id"]): row for row in read_jsonl(output_path)}
    review_fields = (
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "verdict",
        "notes",
        "adjudicated_route",
        "adjudicated_decision",
        "adjudicated_tools",
    )
    queue: list[dict[str, Any]] = []
    preserved = 0
    for source in source_rows:
        row = queue_row(source, kind)
        previous = existing.get(str(source["case_id"]))
        if previous is not None and previous.get("source_row_sha256") == row["source_row_sha256"]:
            for field in review_fields:
                if field in row or field in previous:
                    row[field] = previous.get(field)
            preserved += int(previous.get("review_status") == "reviewed")
        queue.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in queue),
        encoding="utf-8",
    )
    return {
        "kind": kind,
        "source": str(source_path),
        "source_sha256": file_sha256(source_path),
        "rows": len(queue),
        "preserved_reviewed_rows": preserved,
        "output": str(output_path),
    }


def review_errors(row: dict[str, Any], source: dict[str, Any], kind: ReviewKind) -> list[str]:
    errors: list[str] = []
    if row.get("source_row_sha256") != row_sha256(source):
        errors.append("source row hash mismatch")
    if kind == "routing" and (
        row.get("proposed_route") != source.get("expected_route")
        or row.get("proposed_decision") != source.get("expected_decision")
    ):
        errors.append("proposed routing labels do not match source")
    if kind == "tool_selection" and row.get("proposed_tools") != source.get("expected_tools"):
        errors.append("proposed tools do not match source")
    if row.get("review_status") != "reviewed":
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
    verdict = row.get("verdict")
    if verdict not in {"accept", "correct"}:
        errors.append("verdict must be accept or correct")
        return errors
    if kind == "routing" and verdict == "correct":
        route = row.get("adjudicated_route")
        decision = row.get("adjudicated_decision")
        if route is not None and route not in ROUTES:
            errors.append("adjudicated_route is invalid")
        if decision not in DECISIONS:
            errors.append("adjudicated_decision is invalid")
        if decision == "auto_route" and route is None:
            errors.append("auto_route requires an adjudicated route")
        if decision == "clarify" and route is not None:
            errors.append("clarify requires adjudicated_route to be null")
        if decision == "safe_reply" and route != "safe_reply":
            errors.append("safe_reply requires adjudicated_route=safe_reply")
    if kind == "tool_selection" and verdict == "correct":
        tools = row.get("adjudicated_tools")
        if not isinstance(tools, list) or any(tool not in TOOLS for tool in tools):
            errors.append("adjudicated_tools must be a list of known tools")
        elif len(tools) != len(set(tools)):
            errors.append("adjudicated_tools contains duplicates")
    return errors


def adjudicated_row(
    source: dict[str, Any], review: dict[str, Any], kind: ReviewKind
) -> dict[str, Any]:
    value = dict(source)
    corrected = review["verdict"] == "correct"
    if kind == "routing" and corrected:
        value["expected_route"] = review.get("adjudicated_route")
        value["expected_decision"] = review["adjudicated_decision"]
    elif kind == "tool_selection" and corrected:
        value["expected_tools"] = review["adjudicated_tools"]
    value["provenance"] = "independent_human_adjudication"
    value["reviewer_id"] = review["reviewer_id"]
    value["reviewed_at"] = review["reviewed_at"]
    return value


def finalize(
    *,
    source_path: Path,
    queue_path: Path,
    output_path: Path,
    summary_path: Path,
    kind: ReviewKind,
    expected_rows: int,
) -> dict[str, Any]:
    source_rows = read_jsonl(source_path)
    queue_rows = read_jsonl(queue_path)
    sources = {str(row["case_id"]): row for row in source_rows}
    reviews = {str(row["case_id"]): row for row in queue_rows}
    duplicate_source = len(sources) != len(source_rows)
    duplicate_review = len(reviews) != len(queue_rows)
    missing = sorted(set(sources) - set(reviews))
    unexpected = sorted(set(reviews) - set(sources))
    invalid: list[dict[str, Any]] = []
    reviewed = 0
    adjudicated: list[dict[str, Any]] = []
    for case_id, source in sources.items():
        review = reviews.get(case_id)
        if review is None:
            continue
        errors = review_errors(review, source, kind)
        if errors:
            invalid.append({"case_id": case_id, "errors": errors})
        if review.get("review_status") == "reviewed" and not errors:
            reviewed += 1
            adjudicated.append(adjudicated_row(source, review, kind))
    complete = (
        len(source_rows) == expected_rows
        and len(queue_rows) == expected_rows
        and not duplicate_source
        and not duplicate_review
        and not missing
        and not unexpected
        and not invalid
        and reviewed == expected_rows
    )
    if complete:
        content = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in adjudicated
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        output_sha256: str | None = file_sha256(output_path)
    else:
        if output_path.exists():
            output_path.unlink()
        output_sha256 = None
    summary = {
        "schema_version": "1.0",
        "kind": kind,
        "status": "human_gold_verified" if complete else "pending_human_review",
        "source_suite": str(source_path),
        "source_sha256": file_sha256(source_path),
        "expected_rows": expected_rows,
        "queue_rows": len(queue_rows),
        "reviewed": reviewed,
        "pending": expected_rows - reviewed,
        "duplicate_source_ids": duplicate_source,
        "duplicate_review_ids": duplicate_review,
        "missing_case_ids": missing,
        "unexpected_case_ids": unexpected,
        "invalid_reviews": invalid,
        "adjudicated_output": str(output_path),
        "adjudicated_sha256": output_sha256,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary
