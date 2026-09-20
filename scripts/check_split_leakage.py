from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from packages.data_pipeline.provenance import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify frozen split counts and leakage")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/intent_v1.json"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for path_text, expected_hash in manifest["output_sha256"].items():
        path = Path(path_text)
        if sha256_file(path) != expected_hash:
            errors.append(f"Hash mismatch: {path}")
        frames.append(pd.read_parquet(path))
    dataset = pd.concat(frames, ignore_index=True)
    if len(dataset) != 25000:
        errors.append(f"Expected 25000 rows, got {len(dataset)}")
    if dataset["sample_id"].duplicated().any():
        errors.append("sample_id appears in more than one row/split")
    if dataset["normalized_text_hash"].duplicated().any():
        errors.append("normalized text hash leakage detected")

    group_splits: dict[str, set[str]] = defaultdict(set)
    for group_key, split in zip(dataset["group_key"], dataset["split"], strict=True):
        group_splits[str(group_key)].add(str(split))
    leaked_groups = [key for key, splits in group_splits.items() if len(splits) > 1]
    if leaked_groups:
        errors.append(f"{len(leaked_groups)} group keys cross splits")
    for column in ("source_dialogue_id", "template_family", "semantic_cluster_id"):
        usable = dataset[dataset[column].notna()].copy()
        usable = usable[~usable[column].astype(str).isin({"", "none"})]
        crossings = usable.groupby(column)["split"].nunique()
        crossing_count = int((crossings > 1).sum())
        if crossing_count:
            errors.append(f"{crossing_count} values in {column} cross splits")
    actual_splits = dataset["split"].value_counts().to_dict()
    if actual_splits != manifest["split_counts"]:
        errors.append(f"Split counts differ from manifest: {actual_splits}")
    if errors:
        raise SystemExit("Split leakage gate failed:\n- " + "\n- ".join(errors))
    print(f"Split leakage gate passed for {len(dataset)} rows and {len(group_splits)} groups")


if __name__ == "__main__":
    main()
