from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from packages.data_pipeline.provenance import load_manifest, sha256_file


def stable_rank(value: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def assign_connected_group_keys(group: pd.DataFrame) -> pd.DataFrame:
    """Group rows transitively by dialogue, template family, or semantic cluster."""
    group = group.copy()
    parent = list(range(len(group)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    seen: dict[str, int] = {}
    grouping_columns = (
        "source_dialogue_id",
        "template_family",
        "semantic_cluster_id",
    )
    for position, (_, row) in enumerate(group.iterrows()):
        for column in grouping_columns:
            value = row.get(column)
            if pd.isna(value) or not str(value).strip() or str(value) == "none":
                continue
            token = f"{column}:{value}"
            previous = seen.setdefault(token, position)
            union(position, previous)

    member_ids: dict[int, list[str]] = {}
    for position, sample_id in enumerate(group["sample_id"].astype(str)):
        member_ids.setdefault(find(position), []).append(sample_id)
    key_by_root = {
        root: hashlib.sha256("|".join(sorted(values)).encode()).hexdigest()[:20]
        for root, values in member_ids.items()
    }
    group["group_key"] = [key_by_root[find(position)] for position in range(len(group))]
    return group


def assign_label_splits(group: pd.DataFrame, label_total: int, seed: int) -> pd.DataFrame:
    capacities = {
        "train": int(label_total * 0.8),
        "validation": int(label_total * 0.1),
        "test": label_total - int(label_total * 0.8) - int(label_total * 0.1),
    }
    group = assign_connected_group_keys(group)
    grouped = [part for _, part in group.groupby("group_key", sort=False)]
    grouped.sort(
        key=lambda part: (
            -len(part),
            stable_rank(str(part.iloc[0]["group_key"]), seed),
        )
    )
    assignments: list[pd.DataFrame] = []
    remaining = capacities.copy()
    for part in grouped:
        eligible = [split for split, room in remaining.items() if room >= len(part)]
        if not eligible:
            raise RuntimeError(
                f"Cannot assign group {part.iloc[0]['group_key']} of size {len(part)}; "
                f"remaining={remaining}"
            )
        split = max(eligible, key=lambda name: (remaining[name] / capacities[name], name))
        assigned = part.copy()
        assigned["split"] = split
        assignments.append(assigned)
        remaining[split] -= len(part)
    if any(remaining.values()):
        raise RuntimeError(f"Split assignment left unfilled capacity: {remaining}")
    return pd.concat(assignments, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the frozen 25k intent corpus")
    parser.add_argument("--config", type=Path, default=Path("configs/intent/dataset_v1.yaml"))
    parser.add_argument(
        "--candidate-pool-config",
        type=Path,
        default=Path("configs/intent/candidate_pool_v1.yaml"),
    )
    parser.add_argument(
        "--near-duplicate-report",
        type=Path,
        default=Path("reports/data/near_duplicate_report.json"),
    )
    parser.add_argument(
        "--bge-calibration-report",
        type=Path,
        default=Path("reports/data/bge_threshold_calibration.json"),
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/interim/quality_pool.final.parquet")
    )
    parser.add_argument("--output-root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/intent_v1.json"))
    args = parser.parse_args()

    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    frame = pd.read_parquet(args.input)
    seed = int(config["seed"])
    selected_parts: list[pd.DataFrame] = []
    for label, target_raw in config["target_counts"].items():
        target = int(target_raw)
        synthetic_target = int(config["synthetic_counts"][label])
        label_rows = frame[frame["target_intent"] == label].copy()
        label_rows["_rank"] = label_rows["sample_id"].map(lambda value: stable_rank(value, seed))
        synthetic = label_rows[label_rows["source_id"] == "controlled_synthetic"].sort_values(
            "_rank"
        )
        external = label_rows[label_rows["source_id"] != "controlled_synthetic"].sort_values(
            "_rank"
        )
        external_target = target - synthetic_target
        if len(synthetic) < synthetic_target or len(external) < external_target:
            raise SystemExit(
                f"{label} shortage after quality gates: "
                f"synthetic {len(synthetic)}/{synthetic_target}, "
                f"external {len(external)}/{external_target}"
            )
        selected = pd.concat(
            [synthetic.head(synthetic_target), external.head(external_target)],
            ignore_index=True,
        ).drop(columns=["_rank"])
        selected_parts.append(assign_label_splits(selected, target, seed))
        print(f"[{label}] selected {len(selected)}")

    dataset = pd.concat(selected_parts, ignore_index=True)
    if len(dataset) != 25000:
        raise SystemExit(f"Expected 25,000 rows, got {len(dataset)}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    output_files: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        split_frame = dataset[dataset["split"] == split].sort_values("sample_id")
        path = args.output_root / f"{split}.parquet"
        split_frame.to_parquet(path, index=False)
        output_files[path.as_posix()] = sha256_file(path)

    source_manifest = [
        item.model_dump(mode="json")
        for item in load_manifest(Path("data/manifests/data_sources.yaml"))
    ]
    near_duplicate_report = json.loads(args.near_duplicate_report.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "1.0",
        "data_version": config["data_version"],
        "seed": seed,
        "config_sha256": sha256_file(args.config),
        "candidate_pool_config_sha256": sha256_file(args.candidate_pool_config),
        "near_duplicate_report_sha256": sha256_file(args.near_duplicate_report),
        "bge_calibration_report_sha256": sha256_file(args.bge_calibration_report),
        "semantic_deduplication": {
            "model": near_duplicate_report["bge_model"],
            "model_revision": near_duplicate_report["bge_model_revision"],
            "minhash_threshold": near_duplicate_report["minhash_threshold"],
            "bge_similarity_threshold": near_duplicate_report["bge_similarity_threshold"],
            "removal_counts": near_duplicate_report["removal_counts"],
        },
        "source_manifest": source_manifest,
        "row_count": len(dataset),
        "split_counts": dataset["split"].value_counts().to_dict(),
        "label_counts": dataset["target_intent"].value_counts().sort_index().to_dict(),
        "source_counts": dataset["source_id"].value_counts().to_dict(),
        "synthetic_count": int((dataset["source_id"] == "controlled_synthetic").sum()),
        "output_sha256": output_files,
        "pii_status_counts": dataset["pii_status"].value_counts().to_dict(),
        "template_family_max": int(
            dataset[dataset["source_id"] == "controlled_synthetic"]
            .groupby(["target_intent", "template_family"])
            .size()
            .max()
        ),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    printable = {key: value for key, value in manifest.items() if key != "source_manifest"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
