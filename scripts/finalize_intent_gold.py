from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sklearn.metrics import cohen_kappa_score

from packages.data_pipeline.cleaning import detect_pii, normalized_text_hash
from packages.data_pipeline.provenance import sha256_file


def valid_audit_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze a fully adjudicated annotation queue as intent_gold_v1"
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/annotation/gold_annotation_queue_v1.csv")
    )
    parser.add_argument("--config", type=Path, default=Path("configs/intent/dataset_v1.yaml"))
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/intent_gold_v1.parquet")
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/intent_gold_v1.json"))
    args = parser.parse_args()

    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    labels = set(config["target_counts"])
    frame = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    errors: list[str] = []
    required_columns = {
        "annotation_id",
        "double_annotation_required",
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
    }
    missing_columns = sorted(required_columns - set(frame.columns))
    if missing_columns:
        raise SystemExit(
            "Gold freeze gate failed:\n- missing audit columns: " + ", ".join(missing_columns)
        )
    if len(frame) != 1600:
        errors.append(f"expected 1600 rows, got {len(frame)}")
    incomplete = frame[frame["status"] != "adjudicated"]
    if len(incomplete):
        errors.append(f"{len(incomplete)} rows are not adjudicated")
    for column in (
        "adjudicated_text",
        "adjudicated_label",
        "annotator_1_text",
        "annotator_1_label",
        "annotator_1_id",
        "annotator_1_reviewed_at",
        "adjudicator_id",
        "adjudicated_at",
    ):
        empty = int(frame[column].str.strip().eq("").sum())
        if empty:
            errors.append(f"{empty} rows have an empty {column}")
    for column in ("annotator_1_reviewed_at", "adjudicated_at"):
        invalid_timestamps = int(
            (frame[column].str.strip().ne("") & ~frame[column].map(valid_audit_timestamp)).sum()
        )
        if invalid_timestamps:
            errors.append(f"{invalid_timestamps} rows have an invalid {column}")
    for column in ("adjudicated_label", "annotator_1_label"):
        invalid_labels = sorted(set(frame[column]) - labels - {""})
        if invalid_labels:
            errors.append(f"invalid {column} values: {invalid_labels}")

    overlap = frame[frame["double_annotation_required"].str.lower() == "true"]
    if len(overlap) < 320:
        errors.append(f"double-annotation overlap is {len(overlap)}, expected at least 320")
    for column in (
        "annotator_2_text",
        "annotator_2_label",
        "annotator_2_id",
        "annotator_2_reviewed_at",
    ):
        overlap_missing = int(overlap[column].str.strip().eq("").sum())
        if overlap_missing:
            errors.append(f"{overlap_missing} overlap rows are missing {column}")
    invalid_annotator_2_labels = sorted(set(overlap["annotator_2_label"]) - labels - {""})
    if invalid_annotator_2_labels:
        errors.append(f"invalid annotator_2_label values: {invalid_annotator_2_labels}")
    invalid_annotator_2_times = int(
        (
            overlap["annotator_2_reviewed_at"].str.strip().ne("")
            & ~overlap["annotator_2_reviewed_at"].map(valid_audit_timestamp)
        ).sum()
    )
    if invalid_annotator_2_times:
        errors.append(
            f"{invalid_annotator_2_times} overlap rows have an invalid "
            "annotator_2_reviewed_at"
        )
    same_annotator = int(
        (
            overlap["annotator_1_id"].str.strip().ne("")
            & overlap["annotator_2_id"].str.strip().ne("")
            & (overlap["annotator_1_id"] == overlap["annotator_2_id"])
        ).sum()
    )
    if same_annotator:
        errors.append(f"{same_annotator} overlap rows use the same annotator twice")
    overlap_missing = int(overlap["annotator_2_label"].str.strip().eq("").sum())
    kappa = float("nan")
    if not overlap_missing and len(overlap):
        kappa = float(cohen_kappa_score(overlap["annotator_1_label"], overlap["annotator_2_label"]))
        if kappa < 0.80:
            errors.append(f"Cohen's kappa {kappa:.4f} is below 0.80")

    texts = frame["adjudicated_text"].astype(str)
    completed_texts = texts[texts.str.strip().ne("")]
    completed_hashes = completed_texts.map(normalized_text_hash)
    duplicate_count = int(completed_hashes.duplicated().sum())
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate adjudicated texts")
    pii_count = sum(bool(detect_pii(text)) for text in completed_texts)
    if pii_count:
        errors.append(f"{pii_count} adjudicated texts contain residual PII")

    training_hashes: set[str] = set()
    for split in ("train", "validation", "test"):
        processed = pd.read_parquet(
            args.processed_root / f"{split}.parquet", columns=["normalized_text_hash"]
        )
        training_hashes.update(processed["normalized_text_hash"].astype(str))
    leakage_count = len(set(completed_hashes) & training_hashes)
    if leakage_count:
        errors.append(f"{leakage_count} gold texts overlap the 25k corpus")

    label_counts = frame["adjudicated_label"].value_counts().to_dict()
    if not errors and any(label_counts.get(label, 0) != 100 for label in labels):
        errors.append("adjudicated gold labels must contain exactly 100 rows per class")
    if errors:
        raise SystemExit("Gold freeze gate failed:\n- " + "\n- ".join(errors))

    hashes = texts.map(normalized_text_hash)
    output = pd.DataFrame(
        {
            "sample_id": frame["annotation_id"],
            "text_zh": texts,
            "target_intent": frame["adjudicated_label"],
            "requires_independent_human_rewrite": frame["requires_independent_human_rewrite"]
            .str.lower()
            .eq("true"),
            "source": "human_adjudicated_gold",
            "normalized_text_hash": hashes,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(args.output, index=False)
    manifest = {
        "schema_version": "1.0",
        "gold_version": "intent_gold_v1",
        "row_count": len(output),
        "label_counts": output["target_intent"].value_counts().sort_index().to_dict(),
        "double_annotation_rows": len(overlap),
        "cohen_kappa": kappa,
        "independent_human_rewrite_rows": int(output["requires_independent_human_rewrite"].sum()),
        "annotator_1_ids": sorted(set(frame["annotator_1_id"])),
        "annotator_2_ids": sorted(set(overlap["annotator_2_id"])),
        "adjudicator_ids": sorted(set(frame["adjudicator_id"])),
        "training_overlap_count": leakage_count,
        "residual_pii_count": pii_count,
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
