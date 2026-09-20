from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from packages.data_pipeline.cleaning import detect_pii


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail if processed intent data retains PII")
    parser.add_argument("--root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument("--report", type=Path, default=Path("reports/data/intent_v1_pii_scan.json"))
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    affected_ids: list[str] = []
    scanned = 0
    for split in ("train", "validation", "test"):
        frame = pd.read_parquet(args.root / f"{split}.parquet")
        for sample_id, text in zip(frame["sample_id"], frame["text_zh"], strict=True):
            scanned += 1
            matches = detect_pii(str(text))
            if matches:
                counts.update(matches)
                affected_ids.append(str(sample_id))

    report = {
        "scanned_rows": scanned,
        "status": "passed" if not affected_ids else "failed",
        "residual_pii_type_counts": dict(counts),
        "affected_sample_ids": affected_ids,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if affected_ids:
        raise SystemExit(f"PII gate failed for {len(affected_ids)} rows")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
