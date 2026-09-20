from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from packages.data_pipeline.cleaning import clean_text, normalized_text_hash


def load_translations(path: Path) -> dict[str, dict[str, Any]]:
    translations: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            payload = json.loads(line)
            sample_id = str(payload["sample_id"])
            if sample_id in translations:
                raise ValueError(
                    "Translation checkpoint must be compacted before quality filtering; "
                    f"duplicate ID at line {line_number}: {sample_id}"
                )
            translations[sample_id] = payload
    return translations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge translations and apply fail-closed cleaning"
    )
    parser.add_argument(
        "--external", type=Path, default=Path("data/interim/external_selected.parquet")
    )
    parser.add_argument(
        "--translations", type=Path, default=Path("data/interim/translations.jsonl")
    )
    parser.add_argument(
        "--synthetic", type=Path, default=Path("data/interim/synthetic_candidates.parquet")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/quality_pool.exact.parquet")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data/exact_cleaning_report.json")
    )
    args = parser.parse_args()

    translations = load_translations(args.translations)
    external = pd.read_parquet(args.external)
    synthetic = pd.read_parquet(args.synthetic)
    rejection_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []

    for row in external.to_dict(orient="records"):
        if pd.isna(row.get("text_zh")):
            translation = translations.get(str(row["sample_id"]))
            if translation is None:
                rejection_counts["missing_translation"] += 1
                continue
            if not bool(translation.get("entities_preserved")):
                rejection_counts["translation_entity_mismatch"] += 1
                continue
            if translation.get("suspected_issue"):
                # DeepSeek mostly flags source-side typos/profanity here. Those are useful
                # robustness examples; local entity preservation remains the hard gate.
                rejection_counts["translation_flagged_nonblocking"] += 1
            row["text_zh"] = translation["rewrite"]
            row["translation_model"] = translation["translation_model"]
            row["prompt_version"] = translation["prompt_version"]
        rows.append(row)
    rows.extend(synthetic.to_dict(orient="records"))

    cleaned_rows: list[dict[str, Any]] = []
    hash_labels: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        cleaned = clean_text(str(row["text_zh"]))
        if not 2 <= len(cleaned.text) <= 300:
            rejection_counts["length"] += 1
            continue
        row["text_zh"] = cleaned.text
        if cleaned.pii_status == "redacted":
            row["pii_status"] = "redacted"
        text_hash = normalized_text_hash(cleaned.text)
        row["normalized_text_hash"] = text_hash
        hash_labels[text_hash].add(str(row["target_intent"]))
        cleaned_rows.append(row)

    conflicting = {key for key, labels in hash_labels.items() if len(labels) > 1}
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(cleaned_rows, key=lambda item: str(item["sample_id"])):
        text_hash = str(row["normalized_text_hash"])
        if text_hash in conflicting:
            rejection_counts["exact_label_conflict"] += 1
            continue
        key = (str(row["target_intent"]), text_hash)
        if key in unique:
            rejection_counts["exact_duplicate"] += 1
            continue
        unique[key] = row

    output = pd.DataFrame(unique.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(args.output, index=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "input_external": len(external),
        "input_synthetic": len(synthetic),
        "output_rows": len(output),
        "rejections": dict(rejection_counts),
        "exact_conflict_hashes": len(conflicting),
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
