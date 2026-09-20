from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from packages.data_pipeline.cleaning import detect_pii, normalized_text_hash
from packages.data_pipeline.provenance import sha256_file
from scripts.human_review_workspace import GOLD_IMMUTABLE, read_csv, read_jsonl, row_hash

ROOT = Path(__file__).resolve().parents[1]
STATUS = "ai_assisted_verified_for_demo"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def finalize_intent() -> dict[str, Any]:
    source = {
        row["annotation_id"]: row
        for row in read_csv(ROOT / "data/annotation/gold_annotation_queue_v1.csv")
    }
    drafts = read_csv(ROOT / "data/annotation/review_work/ai_drafts/p1_gold_ai_draft.csv")
    if len(drafts) != 1600 or len(source) != 1600:
        raise SystemExit("AI-assisted intent evidence must contain exactly 1,600 rows")
    rows: list[dict[str, Any]] = []
    for draft in drafts:
        source_row = source.get(draft["annotation_id"])
        if source_row is None or draft["source_row_sha256"] != row_hash(source_row, GOLD_IMMUTABLE):
            raise SystemExit(f"stale AI intent draft: {draft['annotation_id']}")
        if draft["draft_status"] != "ai_assisted_draft_not_human_review":
            raise SystemExit(f"invalid AI draft status: {draft['annotation_id']}")
        rows.append(
            {
                "sample_id": draft["annotation_id"],
                "text_zh": draft["ai_suggested_text"],
                "target_intent": draft["ai_suggested_label"],
                "requires_independent_human_rewrite": draft[
                    "requires_independent_human_rewrite"
                ].lower()
                == "true",
                "source": "ai_assisted_project_owner_accepted",
                "normalized_text_hash": normalized_text_hash(draft["ai_suggested_text"]),
            }
        )
    labels = Counter(str(row["target_intent"]) for row in rows)
    if len(labels) != 16 or set(labels.values()) != {100}:
        raise SystemExit(f"AI-assisted intent label counts are invalid: {dict(labels)}")
    hashes = [str(row["normalized_text_hash"]) for row in rows]
    if len(hashes) != len(set(hashes)):
        raise SystemExit("AI-assisted intent texts contain duplicates")
    pii = sum(bool(detect_pii(str(row["text_zh"]))) for row in rows)
    if pii:
        raise SystemExit(f"AI-assisted intent texts contain {pii} PII matches")
    training_hashes: set[str] = set()
    for split in ("train", "validation", "test"):
        frame = pd.read_parquet(
            ROOT / f"data/processed/intent_v1/{split}.parquet",
            columns=["normalized_text_hash"],
        )
        training_hashes.update(frame["normalized_text_hash"].astype(str))
    overlap = len(set(hashes) & training_hashes)
    if overlap:
        raise SystemExit(f"AI-assisted intent texts overlap the 25k corpus: {overlap}")
    output = ROOT / "data/processed/intent_ai_assisted_v1.parquet"
    pd.DataFrame(rows).to_parquet(output, index=False)
    manifest = {
        "schema_version": "1.0",
        "gold_version": "intent_ai_assisted_v1",
        "evaluation_status": STATUS,
        "human_review_status": "not_performed",
        "row_count": len(rows),
        "label_counts": dict(sorted(labels.items())),
        "training_overlap_count": overlap,
        "residual_pii_count": pii,
        "draft_sha256": sha256_file(
            ROOT / "data/annotation/review_work/ai_drafts/p1_gold_ai_draft.csv"
        ),
        "output_sha256": sha256_file(output),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(ROOT / "data/manifests/intent_ai_assisted_v1.json", manifest)
    return manifest


def finalize_challenge() -> dict[str, Any]:
    drafts = read_csv(ROOT / "data/annotation/review_work/ai_drafts/p1_challenge_ai_draft.csv")
    if len(drafts) != 400:
        raise SystemExit("AI-assisted challenge evidence must contain exactly 400 rows")
    rows = [
        {
            "challenge_id": row["challenge_id"],
            "challenge_type": row["challenge_type"],
            "text": row["text"],
            "adjudicated_intents": json.loads(row["ai_suggested_intents"]),
            "provenance": "ai_assisted_project_owner_accepted",
        }
        for row in drafts
    ]
    output = ROOT / "data/processed/intent_challenge_ai_assisted_v1.jsonl"
    write_jsonl(output, rows)
    manifest = {
        "schema_version": "1.0",
        "challenge_version": "intent_challenge_ai_assisted_v1",
        "evaluation_status": STATUS,
        "human_review_status": "not_performed",
        "row_count": len(rows),
        "challenge_type_counts": dict(
            sorted(Counter(row["challenge_type"] for row in rows).items())
        ),
        "output_sha256": sha256_file(output),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(ROOT / "data/manifests/intent_challenge_ai_assisted_v1.json", manifest)
    return manifest


def finalize_suite(kind: str) -> dict[str, Any]:
    if kind == "routing":
        source_path = ROOT / "evals/routing/routing_800_v1.jsonl"
        draft_path = ROOT / "data/annotation/review_work/ai_drafts/p9_routing_ai_draft.csv"
        output = ROOT / "evals/routing/routing_800_v1_ai_assisted.jsonl"
        summary_path = ROOT / "reports/eval/routing_800_v1_ai_assisted_summary.json"
        expected_rows = 800
        source = {row["case_id"]: row for row in read_jsonl(source_path)}
        rows = []
        for draft in read_csv(draft_path):
            original = source[draft["case_id"]]
            if draft["source_row_sha256"] != row_hash(original, original.keys()):
                raise SystemExit(f"stale routing AI draft: {draft['case_id']}")
            value = dict(original)
            value["expected_route"] = draft["ai_adjudicated_route"] or None
            value["expected_decision"] = draft["ai_adjudicated_decision"]
            value["provenance"] = "ai_assisted_project_owner_accepted"
            rows.append(value)
    else:
        source_path = ROOT / "evals/tool_selection/tool_selection_1000_v1.jsonl"
        draft_path = ROOT / "data/annotation/review_work/ai_drafts/p9_tool_selection_ai_draft.csv"
        output = ROOT / "evals/tool_selection/tool_selection_1000_v1_ai_assisted.jsonl"
        summary_path = ROOT / "reports/eval/tool_selection_1000_v1_ai_assisted_summary.json"
        expected_rows = 1000
        source = {row["case_id"]: row for row in read_jsonl(source_path)}
        rows = []
        for draft in read_csv(draft_path):
            original = source[draft["case_id"]]
            if draft["source_row_sha256"] != row_hash(original, original.keys()):
                raise SystemExit(f"stale tool-selection AI draft: {draft['case_id']}")
            value = dict(original)
            value["expected_tools"] = json.loads(draft["ai_adjudicated_tools"])
            value["provenance"] = "ai_assisted_project_owner_accepted"
            rows.append(value)
    if len(rows) != expected_rows:
        raise SystemExit(f"{kind} AI-assisted suite has {len(rows)} rows")
    write_jsonl(output, rows)
    summary = {
        "schema_version": "1.0",
        "kind": kind,
        "status": STATUS,
        "human_review_status": "not_performed",
        "source_sha256": sha256_file(source_path),
        "draft_sha256": sha256_file(draft_path),
        "adjudicated_sha256": sha256_file(output),
        "expected_rows": expected_rows,
        "adjudicated_output": str(output.relative_to(ROOT)),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(summary_path, summary)
    return summary


def finalize_e2e() -> dict[str, Any]:
    drafts = read_csv(ROOT / "data/annotation/review_work/ai_drafts/p9_e2e_ai_draft.csv")
    if len(drafts) != 72:
        raise SystemExit("E2E AI-assisted review must contain exactly 72 rows")
    passed = sum(row["ai_verdict"] == "pass" for row in drafts)
    summary = {
        "schema_version": "1.0",
        "status": STATUS,
        "human_review_status": "not_performed",
        "reviewed": len(drafts),
        "passed": passed,
        "failed": len(drafts) - passed,
        "pass_rate": passed / len(drafts),
        "mean_relevance_score": sum(int(row["ai_relevance_score"]) for row in drafts) / len(drafts),
        "mean_clarity_score": sum(int(row["ai_clarity_score"]) for row in drafts) / len(drafts),
        "draft_sha256": sha256_file(
            ROOT / "data/annotation/review_work/ai_drafts/p9_e2e_ai_draft.csv"
        ),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(ROOT / "reports/eval/e2e_360_v1_ai_assisted_review.json", summary)
    return summary


def main() -> None:
    result = {
        "schema_version": "1.0",
        "status": STATUS,
        "limitations": [
            "The project owner accepted AI-assisted labels for the current demo scope.",
            "No result in this report is independent human annotation or human-gold evidence.",
            "Production release verification remains separate and fail-closed.",
        ],
        "intent": finalize_intent(),
        "challenge": finalize_challenge(),
        "routing": finalize_suite("routing"),
        "tool_selection": finalize_suite("tool_selection"),
        "e2e": finalize_e2e(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(ROOT / "reports/eval/ai_assisted_acceptance_v1.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
