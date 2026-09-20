from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.review_queue import build_queue, finalize

SOURCE = Path("evals/tool_selection/tool_selection_1000_v1.jsonl")
QUEUE = Path("reports/eval/tool_selection_1000_v1_review_queue.jsonl")
ADJUDICATED = Path("evals/tool_selection/tool_selection_1000_v1_human_gold.jsonl")
SUMMARY = Path("reports/eval/tool_selection_1000_v1_review_summary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or finalize tool-selection human review")
    parser.add_argument("action", choices=["build", "finalize"])
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--adjudicated-output", type=Path, default=ADJUDICATED)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args()
    if args.action == "build":
        result = build_queue(args.source, args.queue, "tool_selection")
    else:
        result = finalize(
            source_path=args.source,
            queue_path=args.queue,
            output_path=args.adjudicated_output,
            summary_path=args.summary,
            kind="tool_selection",
            expected_rows=1000,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.action == "finalize" and result["status"] != "human_gold_verified":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
