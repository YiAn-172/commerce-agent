from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from packages.data_pipeline.provenance import sha256_file

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
EXPECTED_TYPES = {
    "multi_intent": 160,
    "low_information": 120,
    "out_of_scope": 80,
    "near_boundary": 40,
}


def valid_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def parse_intents(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError("must be a JSON string array")
    if len(parsed) > 2:
        raise ValueError("must contain at most two intents")
    if len(parsed) != len(set(parsed)):
        raise ValueError("contains duplicate intents")
    unknown = sorted(set(parsed) - LABELS)
    if unknown:
        raise ValueError(f"contains unknown intents: {unknown}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the reviewed intent challenge suite")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/annotation/challenge_annotation_queue_v1.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/intent_challenge_v1.jsonl"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/intent_challenge_v1.json"),
    )
    args = parser.parse_args()
    frame = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    errors: list[str] = []
    required = {
        "challenge_id",
        "challenge_type",
        "text",
        "adjudicated_intents",
        "reviewer_id",
        "reviewed_at",
        "status",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(
            "Challenge freeze gate failed:\n- missing audit columns: " + ", ".join(missing)
        )
    if len(frame) != 400:
        errors.append(f"expected 400 rows, got {len(frame)}")
    counts = Counter(frame["challenge_type"])
    if dict(counts) != EXPECTED_TYPES:
        errors.append(f"challenge type counts do not match: {dict(counts)}")
    duplicate_ids = int(frame["challenge_id"].duplicated().sum())
    if duplicate_ids:
        errors.append(f"{duplicate_ids} duplicate challenge IDs")
    incomplete = int(frame["status"].ne("adjudicated").sum())
    if incomplete:
        errors.append(f"{incomplete} rows are not adjudicated")
    for column in ("reviewer_id", "reviewed_at"):
        empty = int(frame[column].str.strip().eq("").sum())
        if empty:
            errors.append(f"{empty} rows have an empty {column}")
    invalid_times = int(
        (
            frame["reviewed_at"].str.strip().ne("")
            & ~frame["reviewed_at"].map(valid_timestamp)
        ).sum()
    )
    if invalid_times:
        errors.append(f"{invalid_times} rows have an invalid reviewed_at")

    empty_intents = int(frame["adjudicated_intents"].str.strip().eq("").sum())
    if empty_intents:
        errors.append(f"{empty_intents} rows have an empty adjudicated_intents")
    parsed_rows: list[dict[str, Any]] = []
    invalid_intents: list[str] = []
    for row in frame.to_dict(orient="records"):
        if not str(row["adjudicated_intents"]).strip():
            continue
        try:
            intents = parse_intents(str(row["adjudicated_intents"]))
        except (json.JSONDecodeError, ValueError) as error:
            invalid_intents.append(f"{row['challenge_id']}: {error}")
            continue
        parsed_rows.append(
            {
                "challenge_id": row["challenge_id"],
                "challenge_type": row["challenge_type"],
                "text": row["text"],
                "adjudicated_intents": intents,
                "reviewer_id": row["reviewer_id"],
                "reviewed_at": row["reviewed_at"],
                "provenance": "independent_human_review",
            }
        )
    if invalid_intents:
        errors.append(
            f"{len(invalid_intents)} rows have invalid adjudicated_intents; "
            + "; ".join(invalid_intents[:10])
        )
    if errors:
        raise SystemExit("Challenge freeze gate failed:\n- " + "\n- ".join(errors))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in parsed_rows),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "challenge_version": "intent_challenge_v1",
        "evaluation_status": "human_review_verified",
        "row_count": len(parsed_rows),
        "challenge_type_counts": dict(sorted(counts.items())),
        "reviewer_ids": sorted(set(frame["reviewer_id"])),
        "input_sha256": sha256_file(args.input),
        "output_sha256": sha256_file(args.output),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
