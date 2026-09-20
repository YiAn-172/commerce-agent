from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from datasets import DatasetDict, load_from_disk

from packages.data_pipeline.provenance import load_manifest
from packages.data_pipeline.schemas import SourceKind


def profile_huggingface(path: Path) -> dict[str, Any]:
    dataset = load_from_disk(path / "dataset")
    if not isinstance(dataset, DatasetDict):
        dataset = DatasetDict({"train": dataset})

    split_rows = {name: len(split) for name, split in dataset.items()}
    columns = sorted({column for split in dataset.values() for column in split.column_names})
    counters: dict[str, Counter[str]] = {}
    interesting = ("source", "intent", "label_text", "category", "domain", "language")
    for column in interesting:
        if column not in columns:
            continue
        counter: Counter[str] = Counter()
        for split in dataset.values():
            counter.update(str(value) for value in split[column])
        counters[column] = counter

    return {
        "split_rows": split_rows,
        "total_rows": sum(split_rows.values()),
        "columns": columns,
        "value_counts": {
            column: dict(counter.most_common()) for column, counter in counters.items()
        },
    }


def profile_git(path: Path) -> dict[str, Any]:
    files = [item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts]
    extensions = Counter(item.suffix.lower() or "<none>" for item in files)
    line_counts: dict[str, int] = {}
    for item in files:
        if item.suffix.lower() not in {".json", ".jsonl", ".txt", ".csv", ".md"}:
            continue
        try:
            with item.open(encoding="utf-8") as handle:
                line_counts[item.relative_to(path).as_posix()] = sum(1 for _ in handle)
        except UnicodeDecodeError:
            line_counts[item.relative_to(path).as_posix()] = -1
    return {
        "file_count": len(files),
        "extensions": dict(extensions.most_common()),
        "text_file_line_counts": line_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile raw datasets without exposing rows")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data_sources.yaml"))
    parser.add_argument("--output", type=Path, default=Path("reports/data/raw_source_profile.json"))
    args = parser.parse_args()

    report: dict[str, Any] = {"schema_version": "1.0", "sources": {}}
    for record in load_manifest(args.manifest):
        local_path = Path(record.local_path)
        if record.kind == SourceKind.HUGGINGFACE:
            profile = profile_huggingface(local_path)
        else:
            profile = profile_git(local_path)
        report["sources"][record.source_id] = profile
        print(f"[{record.source_id}] profiled")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
