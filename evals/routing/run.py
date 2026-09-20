from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.intent_service.runtime import IntentRuntime
from evals.review_queue import (
    file_sha256,
    verify_ai_assisted_suite,
    verify_human_gold_suite,
)

TEMPLATE_SUITE = "routing_800_v1"
HUMAN_GOLD_SUITE = "routing_800_v1_human_gold"
AI_ASSISTED_SUITE = "routing_800_v1_ai_assisted"
SOURCE_SUITE_PATH = Path("evals/routing/routing_800_v1.jsonl")
HUMAN_GOLD_SUITE_PATH = Path("evals/routing/routing_800_v1_human_gold.jsonl")
AI_ASSISTED_SUITE_PATH = Path("evals/routing/routing_800_v1_ai_assisted.jsonl")
AI_ASSISTED_SUMMARY_PATH = Path("reports/eval/routing_800_v1_ai_assisted_summary.json")
REVIEW_SUMMARY_PATH = Path("reports/eval/routing_800_v1_review_summary.json")
SUITES = {
    TEMPLATE_SUITE: SOURCE_SUITE_PATH,
    HUMAN_GOLD_SUITE: HUMAN_GOLD_SUITE_PATH,
    AI_ASSISTED_SUITE: AI_ASSISTED_SUITE_PATH,
}
ROUTES = ["knowledge", "shopping", "order", "after_sales", "human", "general", "safe_reply"]


def load_suite(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Routing suite is missing: {path}. Run evals.routing.build_suite first.")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    required = {
        "case_id",
        "category",
        "text",
        "expected_route",
        "expected_decision",
        "provenance",
    }
    if len(cases) != 800:
        raise SystemExit(f"routing_800_v1 must contain exactly 800 cases; got {len(cases)}")
    if any(required - set(case) for case in cases):
        raise SystemExit("routing suite contains cases with missing required fields")
    if len({str(case["case_id"]) for case in cases}) != len(cases):
        raise SystemExit("routing suite contains duplicate case IDs")
    if len({str(case["text"]) for case in cases}) != len(cases):
        raise SystemExit("routing suite contains duplicate texts")
    counts = Counter(str(case["category"]) for case in cases)
    if set(counts.values()) != {100} or len(counts) != 8:
        raise SystemExit(f"routing suite must contain eight categories of 100: {dict(counts)}")
    return cases


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def rate(successes: int, total: int) -> dict[str, Any]:
    return {
        "correct": successes,
        "total": total,
        "accuracy": successes / total if total else 0.0,
        "wilson_95_ci": wilson_interval(successes, total),
    }


def evaluate(
    cases: list[dict[str, Any]], runtime: IntentRuntime, batch_size: int
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(cases), batch_size):
        batch = cases[start : start + batch_size]
        predictions = runtime.predict_batch(
            [str(case["text"]) for case in batch],
            [case.get("previous_user_text") for case in batch],
        )
        for case, prediction in zip(batch, predictions, strict=True):
            expected_route = case["expected_route"]
            route_ok = expected_route is None or prediction.route.value == expected_route
            decision_ok = prediction.decision == case["expected_decision"]
            passed = route_ok and decision_ok
            rows.append(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "expected_route": expected_route,
                    "actual_route": prediction.route.value,
                    "expected_decision": case["expected_decision"],
                    "actual_decision": prediction.decision,
                    "predicted_intent": prediction.label.value,
                    "confidence": prediction.confidence,
                    "passed": passed,
                }
            )

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row["category"])].append(row)
    category_metrics = {
        category: rate(sum(bool(row["passed"]) for row in category_rows), len(category_rows))
        for category, category_rows in sorted(by_category.items())
    }
    route_cases = [row for row in rows if row["expected_route"] is not None]
    route_labels = sorted(set(ROUTES) | {str(row["expected_route"]) for row in route_cases})
    confusion = {
        expected: {
            actual: sum(
                row["expected_route"] == expected and row["actual_route"] == actual
                for row in route_cases
            )
            for actual in route_labels
        }
        for expected in route_labels
    }
    failures = [row for row in rows if not row["passed"]]
    return {
        "overall": rate(sum(bool(row["passed"]) for row in rows), len(rows)),
        "route_accuracy": rate(
            sum(row["actual_route"] == row["expected_route"] for row in route_cases),
            len(route_cases),
        ),
        "direct_dispatch_accuracy": category_metrics["direct_dispatch"],
        "clarification_accuracy": category_metrics["clarification"],
        "human_handoff_accuracy": category_metrics["human_handoff"],
        "by_category": category_metrics,
        "confusion_matrix": confusion,
        "failure_count": len(failures),
        "failure_samples": failures[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the frozen routing challenge suite")
    parser.add_argument("--suite", choices=sorted(SUITES), default="routing_800_v1")
    parser.add_argument("--runtime", type=Path, default=Path("models/intent_classifier/current"))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--report", type=Path, default=Path("reports/eval/routing_800_v1.json"))
    args = parser.parse_args()

    suite_path = SUITES[args.suite]
    human_gold = args.suite == HUMAN_GOLD_SUITE
    ai_assisted = args.suite == AI_ASSISTED_SUITE
    if human_gold:
        try:
            verify_human_gold_suite(
                source_path=SOURCE_SUITE_PATH,
                adjudicated_path=suite_path,
                summary_path=REVIEW_SUMMARY_PATH,
                kind="routing",
                expected_rows=800,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
    if ai_assisted:
        try:
            verify_ai_assisted_suite(
                source_path=SOURCE_SUITE_PATH,
                adjudicated_path=suite_path,
                summary_path=AI_ASSISTED_SUMMARY_PATH,
                kind="routing",
                expected_rows=800,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
    cases = load_suite(suite_path)
    runtime = IntentRuntime(args.runtime)
    metrics = evaluate(cases, runtime, args.batch_size)
    suite_bytes = suite_path.read_bytes()
    if human_gold:
        limitations = [
            (
                "The suite labels were independently human-adjudicated; this report "
                "measures the published intent runtime against that frozen suite."
            )
        ]
    elif ai_assisted:
        limitations = [
            "The project owner accepted AI-assisted labels for demo use.",
            "This is not independent human-gold or production accuracy.",
        ]
    else:
        limitations = [
            "This suite is deterministic and template-generated; it is not a human gold set.",
            (
                "Metrics must not be described as production or human-gold accuracy "
                "until independent review."
            ),
        ]
    report = {
        "schema_version": "1.0",
        "suite_version": args.suite,
        "suite_sha256": hashlib.sha256(suite_bytes).hexdigest(),
        "source_suite_sha256": file_sha256(SOURCE_SUITE_PATH),
        "suite_status": (
            "human_gold_verified"
            if human_gold
            else "ai_assisted_verified_for_demo"
            if ai_assisted
            else "template_challenge_unreviewed"
        ),
        "review_summary": (
            str(REVIEW_SUMMARY_PATH)
            if human_gold
            else str(AI_ASSISTED_SUMMARY_PATH)
            if ai_assisted
            else None
        ),
        "model_version": runtime.metadata.model_version,
        "created_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "limitations": limitations,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({"report": str(args.report), **metrics["overall"]}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
