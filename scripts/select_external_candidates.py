from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def stable_rank(sample_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{sample_id}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Select an oversized, reproducible external pool")
    parser.add_argument(
        "--config", type=Path, default=Path("configs/intent/candidate_pool_v1.yaml")
    )
    parser.add_argument(
        "--input", type=Path, default=Path("data/interim/intent_candidates.parquet")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/external_selected.parquet")
    )
    args = parser.parse_args()

    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    frame = pd.read_parquet(args.input)
    seed = int(config["seed"])
    multiplier = float(config["candidate_pool_multiplier"])
    targets = {key: int(value) for key, value in config["target_counts"].items()}
    synthetic = {key: int(value) for key, value in config["synthetic_counts"].items()}
    source_caps = {key: int(value) for key, value in config["source_caps"].items()}
    source_usage: Counter[str] = Counter()
    selected_ids: list[str] = []

    frame = frame.assign(_rank=frame["sample_id"].map(lambda value: stable_rank(value, seed)))
    for label, target_count in targets.items():
        required_final = target_count - synthetic[label]
        pool_target = max(required_final, int(required_final * multiplier))
        label_selected = 0
        preferences: list[str] = config["source_preferences"][label]
        for source_id in preferences:
            room = source_caps[source_id] - source_usage[source_id]
            if room <= 0:
                continue
            candidates = frame[
                (frame["target_intent"] == label)
                & (frame["source_id"] == source_id)
                & (~frame["sample_id"].isin(selected_ids))
            ].sort_values("_rank")
            take = min(pool_target - label_selected, room, len(candidates))
            if take <= 0:
                continue
            chosen = candidates.head(take)
            selected_ids.extend(chosen["sample_id"].tolist())
            source_usage[source_id] += take
            label_selected += take
            if label_selected >= pool_target:
                break
        if label_selected < required_final:
            raise SystemExit(
                f"Insufficient approved candidates for {label}: "
                f"required {required_final}, selected {label_selected}"
            )
        print(f"[{label}] final need={required_final}, selected pool={label_selected}")

    selected = frame[frame["sample_id"].isin(selected_ids)].drop(columns=["_rank"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    selected.to_parquet(args.output, index=False)
    print(f"Source usage: {dict(source_usage)}")
    print(f"Wrote {len(selected)} records to {args.output}")


if __name__ == "__main__":
    main()
