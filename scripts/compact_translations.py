from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def response_hash(payload: dict[str, Any]) -> str:
    comparable = {
        key: payload.get(key)
        for key in (
            "translation",
            "rewrite",
            "entities_preserved",
            "suspected_issue",
            "translation_model",
            "prompt_version",
        )
    }
    encoded = json.dumps(comparable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def quality_rank(payload: dict[str, Any], line_number: int) -> tuple[int, int, int]:
    return (
        int(bool(payload.get("entities_preserved"))),
        int(not payload.get("suspected_issue")),
        line_number,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compact an append-only translation checkpoint to one row per sample"
    )
    parser.add_argument("--input", type=Path, default=Path("data/interim/translations.jsonl"))
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data/translation_compaction.json")
    )
    args = parser.parse_args()

    chosen: dict[str, tuple[dict[str, Any], int]] = {}
    hashes_by_id: dict[str, set[str]] = {}
    issue_counts: Counter[str] = Counter()
    input_rows = 0
    with args.input.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            input_rows += 1
            payload = json.loads(line)
            sample_id = str(payload["sample_id"])
            for required in ("translation", "rewrite", "translation_model", "prompt_version"):
                if not str(payload.get(required, "")).strip():
                    raise ValueError(
                        f"Translation {sample_id!r} is missing {required!r} at line {line_number}"
                    )
            hashes_by_id.setdefault(sample_id, set()).add(response_hash(payload))
            if not bool(payload.get("entities_preserved")):
                issue_counts["entities_not_preserved"] += 1
            if payload.get("suspected_issue"):
                issue_counts["model_reported_issue"] += 1
            previous = chosen.get(sample_id)
            if previous is None or quality_rank(payload, line_number) > quality_rank(
                previous[0], previous[1]
            ):
                chosen[sample_id] = (payload, line_number)

    temporary = args.input.with_suffix(args.input.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for sample_id in sorted(chosen):
            payload = chosen[sample_id][0]
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    temporary.replace(args.input)

    duplicate_ids = sum(1 for values in hashes_by_id.values() if len(values) > 1)
    current_run: dict[str, Any] = {
        "input_rows": input_rows,
        "unique_sample_ids": len(chosen),
        "discarded_duplicate_rows": input_rows - len(chosen),
        "duplicate_ids_with_different_responses": duplicate_ids,
        "observed_issue_rows": dict(issue_counts),
        "selection_policy": (
            "prefer entities_preserved, then no suspected_issue, then latest checkpoint row"
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if args.report.exists():
        previous = json.loads(args.report.read_text(encoding="utf-8"))
        history.extend(previous.get("history", []))
        previous.pop("history", None)
        history.append(previous)
    report = {**current_run, "history": history}
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
