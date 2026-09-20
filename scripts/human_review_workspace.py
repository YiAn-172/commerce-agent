from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / "data/annotation/review_work"
GOLD_QUEUE = ROOT / "data/annotation/gold_annotation_queue_v1.csv"
CHALLENGE_QUEUE = ROOT / "data/annotation/challenge_annotation_queue_v1.csv"
ROUTING_QUEUE = ROOT / "reports/eval/routing_800_v1_review_queue.jsonl"
TOOL_QUEUE = ROOT / "reports/eval/tool_selection_1000_v1_review_queue.jsonl"
E2E_QUEUE = ROOT / "reports/eval/e2e_360_v1_manual_review.jsonl"

P1_COORDINATOR = WORK_ROOT / "p1_gold_coordinator.csv"
P1_ANNOTATOR_1 = WORK_ROOT / "p1_gold_annotator_1_blind.csv"
P1_ANNOTATOR_2 = WORK_ROOT / "p1_gold_annotator_2_blind.csv"
P1_CHALLENGE = WORK_ROOT / "p1_challenge_review.csv"
P9_ROUTING = WORK_ROOT / "p9_routing_review.csv"
P9_TOOL = WORK_ROOT / "p9_tool_selection_review.csv"
P9_E2E = WORK_ROOT / "p9_e2e_review.csv"

LABELS = {
    "product_search",
    "product_recommend",
    "product_compare",
    "product_detail",
    "stock_price",
    "order_status",
    "logistics_tracking",
    "cancel_order",
    "return_exchange",
    "refund_progress",
    "after_sales_eligibility",
    "policy_faq",
    "complaint",
    "human_handoff",
    "chitchat",
    "out_of_scope",
}

GOLD_IMMUTABLE = (
    "annotation_id",
    "stratum_intent",
    "seed_sample_id",
    "seed_text",
    "requires_independent_human_rewrite",
    "double_annotation_required",
)
GOLD_AUDIT_FIELDS = (
    "annotator_1_id",
    "annotator_1_reviewed_at",
    "annotator_2_id",
    "annotator_2_reviewed_at",
    "adjudicator_id",
    "adjudicated_at",
)
GOLD_EDITABLE = (
    "annotator_1_text",
    "annotator_1_label",
    "annotator_2_text",
    "annotator_2_label",
    "adjudicated_text",
    "adjudicated_label",
    "status",
    *GOLD_AUDIT_FIELDS,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fieldnames)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in names})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def row_hash(row: dict[str, Any], fields: Iterable[str]) -> str:
    payload = {field: row.get(field) for field in fields}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def preserve_rows(
    rows: list[dict[str, Any]],
    *,
    path: Path,
    id_field: str,
    hash_field: str,
    editable_fields: Iterable[str],
) -> list[dict[str, Any]]:
    if not path.exists():
        return rows
    previous = {str(row[id_field]): row for row in read_csv(path)}
    for row in rows:
        old = previous.get(str(row[id_field]))
        if old is None or old.get(hash_field) != str(row.get(hash_field, "")):
            continue
        for field in editable_fields:
            if field in old:
                row[field] = old[field]
    return rows


def prepare_p1_gold() -> dict[str, int]:
    source = read_csv(GOLD_QUEUE)
    coordinator_rows: list[dict[str, Any]] = []
    annotator_1_rows: list[dict[str, Any]] = []
    blind_rows: list[dict[str, Any]] = []
    for row in source:
        source_hash = row_hash(row, GOLD_IMMUTABLE)
        coordinator = dict(row)
        coordinator["source_row_sha256"] = source_hash
        for field in GOLD_AUDIT_FIELDS:
            coordinator.setdefault(field, "")
        coordinator_rows.append(coordinator)
        annotator_1_rows.append(
            {
                "annotation_id": row["annotation_id"],
                "seed_text": row["seed_text"],
                "requires_independent_human_rewrite": row[
                    "requires_independent_human_rewrite"
                ],
                "double_annotation_required": row["double_annotation_required"],
                "source_row_sha256": source_hash,
                "annotator_1_text": row.get("annotator_1_text", ""),
                "annotator_1_label": row.get("annotator_1_label", ""),
                "annotator_1_id": row.get("annotator_1_id", ""),
                "annotator_1_reviewed_at": row.get("annotator_1_reviewed_at", ""),
            }
        )
        if row["double_annotation_required"].lower() == "true":
            blind_rows.append(
                {
                    "annotation_id": row["annotation_id"],
                    "seed_text": row["seed_text"],
                    "requires_independent_human_rewrite": row[
                        "requires_independent_human_rewrite"
                    ],
                    "source_row_sha256": source_hash,
                    "annotator_2_text": row.get("annotator_2_text", ""),
                    "annotator_2_label": row.get("annotator_2_label", ""),
                    "annotator_2_id": row.get("annotator_2_id", ""),
                    "annotator_2_reviewed_at": row.get("annotator_2_reviewed_at", ""),
                }
            )
    annotator_1_rows = preserve_rows(
        annotator_1_rows,
        path=P1_ANNOTATOR_1,
        id_field="annotation_id",
        hash_field="source_row_sha256",
        editable_fields=(
            "annotator_1_text",
            "annotator_1_label",
            "annotator_1_id",
            "annotator_1_reviewed_at",
        ),
    )
    for index, row in enumerate(coordinator_rows):
        row["batch_id"] = f"P1-COORD-{index // 50 + 1:02d}"
    for index, row in enumerate(annotator_1_rows):
        row["batch_id"] = f"P1-A1-{index // 50 + 1:02d}"
    for index, row in enumerate(blind_rows):
        row["batch_id"] = f"P1-A2-{index // 40 + 1:02d}"
    coordinator_rows = preserve_rows(
        coordinator_rows,
        path=P1_COORDINATOR,
        id_field="annotation_id",
        hash_field="source_row_sha256",
        editable_fields=(
            "adjudicated_text",
            "adjudicated_label",
            "adjudicator_id",
            "adjudicated_at",
            "status",
        ),
    )
    blind_rows = preserve_rows(
        blind_rows,
        path=P1_ANNOTATOR_2,
        id_field="annotation_id",
        hash_field="source_row_sha256",
        editable_fields=(
            "annotator_2_text",
            "annotator_2_label",
            "annotator_2_id",
            "annotator_2_reviewed_at",
        ),
    )
    coordinator_fields = [
        "batch_id",
        *GOLD_IMMUTABLE,
        "source_row_sha256",
        "annotator_1_text",
        "annotator_1_label",
        "annotator_1_id",
        "annotator_1_reviewed_at",
        "annotator_2_text",
        "annotator_2_label",
        "annotator_2_id",
        "annotator_2_reviewed_at",
        "adjudicated_text",
        "adjudicated_label",
        "adjudicator_id",
        "adjudicated_at",
        "status",
    ]
    annotator_1_fields = [
        "batch_id",
        "annotation_id",
        "seed_text",
        "requires_independent_human_rewrite",
        "double_annotation_required",
        "source_row_sha256",
        "annotator_1_text",
        "annotator_1_label",
        "annotator_1_id",
        "annotator_1_reviewed_at",
    ]
    blind_fields = [
        "batch_id",
        "annotation_id",
        "seed_text",
        "requires_independent_human_rewrite",
        "source_row_sha256",
        "annotator_2_text",
        "annotator_2_label",
        "annotator_2_id",
        "annotator_2_reviewed_at",
    ]
    write_csv(P1_COORDINATOR, coordinator_rows, coordinator_fields)
    write_csv(P1_ANNOTATOR_1, annotator_1_rows, annotator_1_fields)
    write_csv(P1_ANNOTATOR_2, blind_rows, blind_fields)
    return {
        "coordinator_rows": len(coordinator_rows),
        "annotator_1_rows": len(annotator_1_rows),
        "annotator_2_rows": len(blind_rows),
    }


def prepare_challenge() -> int:
    source = read_csv(CHALLENGE_QUEUE)
    fields = [
        "batch_id",
        "challenge_id",
        "challenge_type",
        "text",
        "source_sample_ids",
        "suggested_intents",
        "source_row_sha256",
        "adjudicated_intents",
        "reviewer_id",
        "reviewed_at",
        "notes",
        "status",
    ]
    rows: list[dict[str, Any]] = []
    for row in source:
        value = dict(row)
        value["source_row_sha256"] = row_hash(
            row,
            ("challenge_id", "challenge_type", "text", "source_sample_ids", "suggested_intents"),
        )
        value.setdefault("reviewer_id", "")
        value.setdefault("reviewed_at", "")
        value.setdefault("notes", "")
        rows.append(value)
    for index, row in enumerate(rows):
        row["batch_id"] = f"P1-CH-{index // 50 + 1:02d}"
    rows = preserve_rows(
        rows,
        path=P1_CHALLENGE,
        id_field="challenge_id",
        hash_field="source_row_sha256",
        editable_fields=("adjudicated_intents", "reviewer_id", "reviewed_at", "notes", "status"),
    )
    write_csv(P1_CHALLENGE, rows, fields)
    return len(rows)


def prepare_p9() -> dict[str, int]:
    routing_source = read_jsonl(ROUTING_QUEUE)
    routing_fields = [
        "batch_id",
        "case_id",
        "category",
        "text",
        "previous_user_text",
        "source_row_sha256",
        "proposed_route",
        "proposed_decision",
        "adjudicated_route",
        "adjudicated_decision",
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "verdict",
        "notes",
    ]
    routing_rows = [dict(row) for row in routing_source]
    for index, row in enumerate(routing_rows):
        row["batch_id"] = f"P9-ROUTE-{index // 50 + 1:02d}"
    routing_rows = preserve_rows(
        routing_rows,
        path=P9_ROUTING,
        id_field="case_id",
        hash_field="source_row_sha256",
        editable_fields=(
            "adjudicated_route",
            "adjudicated_decision",
            "review_status",
            "reviewer_id",
            "reviewed_at",
            "verdict",
            "notes",
        ),
    )
    write_csv(P9_ROUTING, routing_rows, routing_fields)

    tool_source = read_jsonl(TOOL_QUEUE)
    tool_rows: list[dict[str, Any]] = []
    for row in tool_source:
        value = dict(row)
        value["proposed_tools"] = json.dumps(row.get("proposed_tools", []), ensure_ascii=False)
        adjudicated = row.get("adjudicated_tools")
        value["adjudicated_tools"] = (
            json.dumps(adjudicated, ensure_ascii=False) if adjudicated is not None else ""
        )
        tool_rows.append(value)
    for index, row in enumerate(tool_rows):
        row["batch_id"] = f"P9-TOOL-{index // 50 + 1:02d}"
    tool_rows = preserve_rows(
        tool_rows,
        path=P9_TOOL,
        id_field="case_id",
        hash_field="source_row_sha256",
        editable_fields=(
            "adjudicated_tools",
            "review_status",
            "reviewer_id",
            "reviewed_at",
            "verdict",
            "notes",
        ),
    )
    tool_fields = [
        "batch_id",
        "case_id",
        "category",
        "text",
        "previous_user_text",
        "source_row_sha256",
        "proposed_tools",
        "adjudicated_tools",
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "verdict",
        "notes",
    ]
    write_csv(P9_TOOL, tool_rows, tool_fields)

    e2e_source = read_jsonl(E2E_QUEUE)
    e2e_rows: list[dict[str, Any]] = []
    for row in e2e_source:
        value = dict(row)
        value["conversation"] = "\n".join(
            f"Turn {turn['turn']} 用户: {turn['user_text']}\n"
            f"Turn {turn['turn']} Agent: {turn['assistant_answer']}\n"
            f"route={turn['route']}; status={turn['status']}; tools={turn['tools']}"
            for turn in row["turns"]
        )
        e2e_rows.append(value)
    for index, row in enumerate(e2e_rows):
        row["batch_id"] = f"P9-E2E-{index // 12 + 1:02d}"
    e2e_rows = preserve_rows(
        e2e_rows,
        path=P9_E2E,
        id_field="case_id",
        hash_field="review_input_sha256",
        editable_fields=(
            "review_status",
            "reviewer_id",
            "reviewed_at",
            "relevance_score",
            "clarity_score",
            "safe_and_helpful",
            "verdict",
            "notes",
        ),
    )
    e2e_fields = [
        "batch_id",
        "case_id",
        "category",
        "scenario",
        "conversation",
        "review_input_sha256",
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "relevance_score",
        "clarity_score",
        "safe_and_helpful",
        "verdict",
        "notes",
    ]
    write_csv(P9_E2E, e2e_rows, e2e_fields)
    return {
        "routing_rows": len(routing_rows),
        "tool_selection_rows": len(tool_rows),
        "e2e_rows": len(e2e_rows),
    }


def valid_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def merge_p1() -> dict[str, int]:
    source = read_csv(GOLD_QUEUE)
    coordinator = {row["annotation_id"]: row for row in read_csv(P1_COORDINATOR)}
    annotator_1 = {row["annotation_id"]: row for row in read_csv(P1_ANNOTATOR_1)}
    annotator_2 = {row["annotation_id"]: row for row in read_csv(P1_ANNOTATOR_2)}
    output: list[dict[str, str]] = []
    for row in source:
        case_id = row["annotation_id"]
        work = coordinator.get(case_id)
        if work is None or work.get("source_row_sha256") != row_hash(row, GOLD_IMMUTABLE):
            raise SystemExit(f"P1 coordinator row is missing or stale: {case_id}")
        first = annotator_1.get(case_id)
        if first is None or first.get("source_row_sha256") != row_hash(row, GOLD_IMMUTABLE):
            raise SystemExit(f"P1 annotator-1 row is missing or stale: {case_id}")
        merged = dict(row)
        for field in (
            "annotator_1_text",
            "annotator_1_label",
            "annotator_1_id",
            "annotator_1_reviewed_at",
        ):
            merged[field] = first.get(field, "")
        for field in (
            "adjudicated_text",
            "adjudicated_label",
            "adjudicator_id",
            "adjudicated_at",
            "status",
        ):
            merged[field] = work.get(field, merged.get(field, ""))
        if row["double_annotation_required"].lower() == "true":
            second = annotator_2.get(case_id)
            if second is None or second.get("source_row_sha256") != row_hash(row, GOLD_IMMUTABLE):
                raise SystemExit(f"P1 annotator-2 row is missing or stale: {case_id}")
            for field in (
                "annotator_2_text",
                "annotator_2_label",
                "annotator_2_id",
                "annotator_2_reviewed_at",
            ):
                merged[field] = second.get(field, "")
        output.append(merged)
    fields = [*source[0].keys()]
    for field in GOLD_AUDIT_FIELDS:
        if field not in fields:
            fields.append(field)
    write_csv(GOLD_QUEUE, output, fields)
    return gold_status(output)


def merge_challenge() -> dict[str, int]:
    source = read_csv(CHALLENGE_QUEUE)
    work = {row["challenge_id"]: row for row in read_csv(P1_CHALLENGE)}
    fields = [*source[0].keys(), "reviewer_id", "reviewed_at", "notes"]
    output: list[dict[str, str]] = []
    for row in source:
        case_id = row["challenge_id"]
        review = work.get(case_id)
        expected_hash = row_hash(
            row,
            ("challenge_id", "challenge_type", "text", "source_sample_ids", "suggested_intents"),
        )
        if review is None or review.get("source_row_sha256") != expected_hash:
            raise SystemExit(f"P1 challenge row is missing or stale: {case_id}")
        merged = dict(row)
        for field in ("adjudicated_intents", "reviewer_id", "reviewed_at", "notes", "status"):
            merged[field] = review.get(field, "")
        output.append(merged)
    write_csv(CHALLENGE_QUEUE, output, fields)
    return challenge_status(output)


def optional(value: str) -> str | None:
    stripped = value.strip()
    return stripped if stripped else None


def parse_tools(value: str) -> list[str] | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ValueError("adjudicated_tools must be a JSON string array")
        return parsed
    return [item.strip() for item in stripped.split(",") if item.strip()]


def merge_jsonl_review(
    *,
    source_path: Path,
    work_path: Path,
    id_field: str,
    hash_field: str,
    editable_fields: tuple[str, ...],
    tool_fields: bool = False,
    e2e_fields: bool = False,
) -> int:
    source = read_jsonl(source_path)
    work = {row[id_field]: row for row in read_csv(work_path)}
    reviewed = 0
    for row in source:
        case_id = str(row[id_field])
        review = work.get(case_id)
        if review is None or review.get(hash_field) != str(row.get(hash_field, "")):
            raise SystemExit(f"review row is missing or stale: {case_id}")
        for field in editable_fields:
            value: Any = optional(review.get(field, ""))
            if tool_fields and field == "adjudicated_tools":
                value = parse_tools(review.get(field, ""))
            if e2e_fields and field in {"relevance_score", "clarity_score"}:
                value = int(value) if value is not None else None
            if e2e_fields and field == "safe_and_helpful":
                if value is None:
                    pass
                elif str(value).lower() in {"true", "1", "yes", "是"}:
                    value = True
                elif str(value).lower() in {"false", "0", "no", "否"}:
                    value = False
                else:
                    raise SystemExit(f"invalid safe_and_helpful for {case_id}: {value}")
            row[field] = value
        reviewed += int(row.get("review_status") == "reviewed")
    write_jsonl(source_path, source)
    return reviewed


def gold_status(rows: list[dict[str, str]]) -> dict[str, int]:
    annotator_1 = sum(
        row.get("annotator_1_text", "").strip() != ""
        and row.get("annotator_1_label", "") in LABELS
        and row.get("annotator_1_id", "").strip() != ""
        and valid_timestamp(row.get("annotator_1_reviewed_at", ""))
        for row in rows
    )
    overlap = [row for row in rows if row["double_annotation_required"].lower() == "true"]
    annotator_2 = sum(
        row.get("annotator_2_text", "").strip() != ""
        and row.get("annotator_2_label", "") in LABELS
        and row.get("annotator_2_id", "").strip() != ""
        and valid_timestamp(row.get("annotator_2_reviewed_at", ""))
        for row in overlap
    )
    adjudicated = sum(
        row.get("status") == "adjudicated"
        and row.get("adjudicated_text", "").strip() != ""
        and row.get("adjudicated_label", "") in LABELS
        and row.get("adjudicator_id", "").strip() != ""
        and valid_timestamp(row.get("adjudicated_at", ""))
        for row in rows
    )
    return {
        "annotator_1_complete": annotator_1,
        "annotator_1_total": len(rows),
        "annotator_2_complete": annotator_2,
        "annotator_2_total": len(overlap),
        "adjudicated": adjudicated,
        "adjudication_total": len(rows),
    }


def challenge_status(rows: list[dict[str, str]]) -> dict[str, int]:
    reviewed = sum(
        row.get("status") == "adjudicated"
        and row.get("adjudicated_intents", "").strip() != ""
        and row.get("reviewer_id", "").strip() != ""
        and valid_timestamp(row.get("reviewed_at", ""))
        for row in rows
    )
    return {"reviewed": reviewed, "total": len(rows)}


def status() -> dict[str, Any]:
    result: dict[str, Any] = {}
    if P1_COORDINATOR.exists() and P1_ANNOTATOR_1.exists() and P1_ANNOTATOR_2.exists():
        coordinator = {row["annotation_id"]: row for row in read_csv(P1_COORDINATOR)}
        first = {row["annotation_id"]: row for row in read_csv(P1_ANNOTATOR_1)}
        second = {row["annotation_id"]: row for row in read_csv(P1_ANNOTATOR_2)}
        combined: list[dict[str, str]] = []
        for case_id, row in coordinator.items():
            value = dict(row)
            value.update(
                {
                    field: first.get(case_id, {}).get(field, "")
                    for field in (
                        "annotator_1_text",
                        "annotator_1_label",
                        "annotator_1_id",
                        "annotator_1_reviewed_at",
                    )
                }
            )
            value.update(
                {
                    field: second.get(case_id, {}).get(field, "")
                    for field in (
                        "annotator_2_text",
                        "annotator_2_label",
                        "annotator_2_id",
                        "annotator_2_reviewed_at",
                    )
                }
            )
            combined.append(value)
        result["p1_gold"] = gold_status(combined)
    else:
        result["p1_gold"] = "run prepare"
    if P1_CHALLENGE.exists():
        result["p1_challenge"] = challenge_status(read_csv(P1_CHALLENGE))
    else:
        result["p1_challenge"] = "run prepare"
    for name, path in (
        ("p9_routing", P9_ROUTING),
        ("p9_tool_selection", P9_TOOL),
        ("p9_e2e", P9_E2E),
    ):
        if not path.exists():
            result[name] = "run prepare"
            continue
        rows = read_csv(path)
        result[name] = {
            "reviewed": sum(row.get("review_status") == "reviewed" for row in rows),
            "total": len(rows),
        }
    return result


def prepare() -> dict[str, Any]:
    return {
        "work_root": str(WORK_ROOT),
        "p1_gold": prepare_p1_gold(),
        "p1_challenge_rows": prepare_challenge(),
        "p9": prepare_p9(),
    }


def sync() -> dict[str, Any]:
    result = {
        "p1_gold": merge_p1(),
        "p1_challenge": merge_challenge(),
        "p9_routing_reviewed": merge_jsonl_review(
            source_path=ROUTING_QUEUE,
            work_path=P9_ROUTING,
            id_field="case_id",
            hash_field="source_row_sha256",
            editable_fields=(
                "adjudicated_route",
                "adjudicated_decision",
                "review_status",
                "reviewer_id",
                "reviewed_at",
                "verdict",
                "notes",
            ),
        ),
        "p9_tool_selection_reviewed": merge_jsonl_review(
            source_path=TOOL_QUEUE,
            work_path=P9_TOOL,
            id_field="case_id",
            hash_field="source_row_sha256",
            editable_fields=(
                "adjudicated_tools",
                "review_status",
                "reviewer_id",
                "reviewed_at",
                "verdict",
                "notes",
            ),
            tool_fields=True,
        ),
        "p9_e2e_reviewed": merge_jsonl_review(
            source_path=E2E_QUEUE,
            work_path=P9_E2E,
            id_field="case_id",
            hash_field="review_input_sha256",
            editable_fields=(
                "review_status",
                "reviewer_id",
                "reviewed_at",
                "relevance_score",
                "clarity_score",
                "safe_and_helpful",
                "verdict",
                "notes",
            ),
            e2e_fields=True,
        ),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and synchronize human-review CSV files")
    parser.add_argument("action", choices=["prepare", "status", "sync"])
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare()
    elif args.action == "sync":
        result = sync()
    else:
        result = status()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
