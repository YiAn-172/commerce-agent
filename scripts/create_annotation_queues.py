from __future__ import annotations

import argparse
import hashlib
import json
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def stable_rank(value: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def processed_sample_ids(root: Path) -> set[str]:
    result: set[str] = set()
    for split in ("train", "validation", "test"):
        frame = pd.read_parquet(root / f"{split}.parquet", columns=["sample_id"])
        result.update(frame["sample_id"].astype(str))
    return result


def make_gold_queue(
    pool: pd.DataFrame, labels: list[str], seed: int
) -> tuple[pd.DataFrame, set[str]]:
    rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for label in labels:
        candidates = pool[pool["target_intent"] == label].copy()
        candidates["_rank"] = (
            candidates["sample_id"]
            .astype(str)
            .map(lambda value: stable_rank(f"gold:{value}", seed))
        )
        candidates = candidates.sort_values("_rank").head(80)
        candidate_count = len(candidates)
        if candidate_count == 0:
            raise RuntimeError(f"No unused gold candidates remain for {label}")
        candidate_overlap = min(16, candidate_count)
        for index, candidate in enumerate(candidates.to_dict(orient="records")):
            sample_id = str(candidate["sample_id"])
            used_ids.add(sample_id)
            rows.append(
                {
                    "annotation_id": f"gold-{label}-{index:03d}",
                    "stratum_intent": label,
                    "seed_sample_id": sample_id,
                    "seed_text": str(candidate["text_zh"]),
                    "requires_independent_human_rewrite": False,
                    "double_annotation_required": index < candidate_overlap,
                    "annotator_1_text": "",
                    "annotator_1_label": "",
                    "annotator_1_id": "",
                    "annotator_1_reviewed_at": "",
                    "annotator_2_text": "",
                    "annotator_2_label": "",
                    "annotator_2_id": "",
                    "annotator_2_reviewed_at": "",
                    "adjudicated_text": "",
                    "adjudicated_label": "",
                    "adjudicator_id": "",
                    "adjudicated_at": "",
                    "status": "pending_human_review",
                }
            )
        rewrite_count = 100 - candidate_count
        rewrite_overlap = 20 - candidate_overlap
        for index in range(rewrite_count):
            rows.append(
                {
                    "annotation_id": f"gold-{label}-rewrite-{index:03d}",
                    "stratum_intent": label,
                    "seed_sample_id": "",
                    "seed_text": "",
                    "requires_independent_human_rewrite": True,
                    "double_annotation_required": index < rewrite_overlap,
                    "annotator_1_text": "",
                    "annotator_1_label": "",
                    "annotator_1_id": "",
                    "annotator_1_reviewed_at": "",
                    "annotator_2_text": "",
                    "annotator_2_label": "",
                    "annotator_2_id": "",
                    "annotator_2_reviewed_at": "",
                    "adjudicated_text": "",
                    "adjudicated_label": "",
                    "adjudicator_id": "",
                    "adjudicated_at": "",
                    "status": "pending_human_review",
                }
            )
    return pd.DataFrame(rows), used_ids


def make_challenge_queue(pool: pd.DataFrame, seed: int) -> pd.DataFrame:
    ranked = pool.copy()
    ranked["_rank"] = (
        ranked["sample_id"].astype(str).map(lambda value: stable_rank(f"challenge:{value}", seed))
    )
    ranked = ranked.sort_values("_rank")
    by_label = {
        str(label): group.to_dict(orient="records")
        for label, group in ranked.groupby("target_intent", sort=True)
    }
    labels = sorted(by_label)
    cursors = {label: 0 for label in labels}
    rows: list[dict[str, Any]] = []

    for index in range(160):
        left_label = labels[index % len(labels)]
        right_label = labels[(index * 7 + 5) % len(labels)]
        if right_label == left_label:
            right_label = labels[(labels.index(right_label) + 1) % len(labels)]
        left = by_label[left_label][cursors[left_label] % len(by_label[left_label])]
        cursors[left_label] += 1
        right = by_label[right_label][cursors[right_label] % len(by_label[right_label])]
        cursors[right_label] += 1
        rows.append(
            {
                "challenge_id": f"challenge-multi-{index:03d}",
                "challenge_type": "multi_intent",
                "text": f"{left['text_zh']}，另外{right['text_zh']}",
                "source_sample_ids": json.dumps(
                    [str(left["sample_id"]), str(right["sample_id"])],
                    ensure_ascii=False,
                ),
                "suggested_intents": json.dumps([left_label, right_label], ensure_ascii=False),
                "adjudicated_intents": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "notes": "",
                "status": "pending_human_review",
            }
        )

    stems = ["这个", "那个", "上一个", "刚才说的", "订单", "商品", "退款", "快递"]
    actions = ["呢", "怎么办", "可以吗", "怎么回事", "帮我看看"]
    tones = ["", "?", "...", "急", "麻烦了"]
    low_information = ["".join(parts) for parts in product(stems, actions, tones)]
    for index, text in enumerate(low_information[:120]):
        rows.append(
            {
                "challenge_id": f"challenge-low-info-{index:03d}",
                "challenge_type": "low_information",
                "text": text,
                "source_sample_ids": "[]",
                "suggested_intents": "[]",
                "adjudicated_intents": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "notes": "",
                "status": "pending_human_review",
            }
        )

    oos = ranked[ranked["target_intent"] == "out_of_scope"].head(80)
    if len(oos) != 80:
        raise RuntimeError(f"Need 80 OOS challenge candidates, got {len(oos)}")
    for index, candidate in enumerate(oos.to_dict(orient="records")):
        rows.append(
            {
                "challenge_id": f"challenge-oos-{index:03d}",
                "challenge_type": "out_of_scope",
                "text": str(candidate["text_zh"]),
                "source_sample_ids": json.dumps([str(candidate["sample_id"])]),
                "suggested_intents": json.dumps(["out_of_scope"]),
                "adjudicated_intents": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "notes": "",
                "status": "pending_human_review",
            }
        )

    boundary_pairs = (
        ("product_search", "product_recommend"),
        ("order_status", "logistics_tracking"),
        ("return_exchange", "refund_progress"),
        ("policy_faq", "after_sales_eligibility"),
    )
    for index in range(40):
        expected, contrast = boundary_pairs[index % len(boundary_pairs)]
        candidate = by_label[expected][cursors[expected] % len(by_label[expected])]
        cursors[expected] += 1
        rows.append(
            {
                "challenge_id": f"challenge-boundary-{index:03d}",
                "challenge_type": "near_boundary",
                "text": str(candidate["text_zh"]),
                "source_sample_ids": json.dumps([str(candidate["sample_id"])]),
                "suggested_intents": json.dumps([expected, contrast]),
                "adjudicated_intents": "",
                "reviewer_id": "",
                "reviewed_at": "",
                "notes": "",
                "status": "pending_human_review",
            }
        )
    if len(rows) != 400:
        raise AssertionError(f"Expected 400 challenge rows, got {len(rows)}")
    return pd.DataFrame(rows)


def write_frame(frame: pd.DataFrame, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(stem.with_suffix(".parquet"), index=False)
    frame.to_csv(stem.with_suffix(".csv"), index=False, encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create pending human annotation queues")
    parser.add_argument("--config", type=Path, default=Path("configs/intent/dataset_v1.yaml"))
    parser.add_argument(
        "--quality-pool", type=Path, default=Path("data/interim/quality_pool.final.parquet")
    )
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument("--output-root", type=Path, default=Path("data/annotation"))
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data/annotation_queue_report.json")
    )
    args = parser.parse_args()

    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    labels = list(config["target_counts"])
    seed = int(config["seed"])
    quality_pool = pd.read_parquet(args.quality_pool)
    unused = quality_pool[
        ~quality_pool["sample_id"].isin(processed_sample_ids(args.processed_root))
    ]
    gold, gold_seed_ids = make_gold_queue(unused, labels, seed)
    challenge_pool = unused[~unused["sample_id"].isin(gold_seed_ids)]
    challenge = make_challenge_queue(challenge_pool, seed)
    write_frame(gold, args.output_root / "gold_annotation_queue_v1")
    write_frame(challenge, args.output_root / "challenge_annotation_queue_v1")

    report = {
        "gold_queue_rows": len(gold),
        "gold_rows_per_stratum": gold["stratum_intent"].value_counts().sort_index().to_dict(),
        "independent_human_rewrite_rows": int(gold["requires_independent_human_rewrite"].sum()),
        "double_annotation_rows": int(gold["double_annotation_required"].sum()),
        "challenge_queue_rows": len(challenge),
        "challenge_type_counts": challenge["challenge_type"].value_counts().to_dict(),
        "status": "pending_human_review_not_gold",
        "freeze_gate": (
            "Do not rename to intent_gold_v1 or use for final metrics until two-person "
            "overlap, adjudication, kappa calculation, and leakage checks pass."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
