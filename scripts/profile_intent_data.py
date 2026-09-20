from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reproducible intent-data profile")
    parser.add_argument("--root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument("--output", type=Path, default=Path("reports/data/intent_v1.html"))
    args = parser.parse_args()

    frames = [
        pd.read_parquet(args.root / f"{split}.parquet") for split in ("train", "validation", "test")
    ]
    frame = pd.concat(frames, ignore_index=True)
    lengths = frame["text_zh"].astype(str).str.len()
    report: dict[str, Any] = {
        "row_count": len(frame),
        "split_counts": frame["split"].value_counts().to_dict(),
        "label_counts": frame["target_intent"].value_counts().sort_index().to_dict(),
        "source_counts": frame["source_id"].value_counts().to_dict(),
        "provenance_counts": frame["provenance"].value_counts().to_dict(),
        "pii_status_counts": frame["pii_status"].value_counts().to_dict(),
        "translation_count": int(frame["translation_model"].notna().sum()),
        "synthetic_count": int((frame["source_id"] == "controlled_synthetic").sum()),
        "length": {
            "min": int(lengths.min()),
            "p50": float(lengths.quantile(0.50)),
            "p95": float(lengths.quantile(0.95)),
            "p99": float(lengths.quantile(0.99)),
            "max": int(lengths.max()),
        },
        "duplicate_sample_ids": int(frame["sample_id"].duplicated().sum()),
        "duplicate_text_hashes": int(frame["normalized_text_hash"].duplicated().sum()),
    }
    json_path = args.output.with_suffix(".json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tables = []
    for key in ("split_counts", "label_counts", "source_counts", "pii_status_counts"):
        table = pd.Series(report[key], name="count").rename_axis(key).to_frame().to_html()
        tables.append(f"<h2>{key}</h2>{table}")
    html = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<title>Intent v1 Data Profile</title><body><h1>Intent v1 Data Profile</h1>"
        f"<pre>{json.dumps(report['length'], ensure_ascii=False, indent=2)}</pre>"
        + "".join(tables)
        + "</body></html>"
    )
    args.output.write_text(html, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
