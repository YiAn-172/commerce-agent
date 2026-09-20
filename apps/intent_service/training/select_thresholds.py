from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select routing thresholds from validation")
    parser.add_argument(
        "--calibration-report",
        type=Path,
        default=Path("reports/training/calibration_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/routing/thresholds.intent_v1.yaml"),
    )
    parser.add_argument("--target-coverage", type=float, default=0.80)
    parser.add_argument("--target-selective-accuracy", type=float, default=0.96)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report: dict[str, Any] = json.loads(args.calibration_report.read_text(encoding="utf-8"))
    curve = report["validation"]["coverage_risk_curve"]
    routing_grid = report["validation"]["routing_grid"]
    feasible = [
        row
        for row in routing_grid
        if float(row["coverage"]) >= args.target_coverage
        and float(row["selective_accuracy"]) >= args.target_selective_accuracy
    ]
    target_met = bool(feasible)
    if feasible:
        selected = max(
            feasible,
            key=lambda row: (
                float(row["selective_accuracy"]),
                float(row["coverage"]),
                float(row["confidence_threshold"]),
                float(row["margin_threshold"]),
            ),
        )
    else:
        coverage_feasible = [
            row for row in routing_grid if float(row["coverage"]) >= args.target_coverage
        ]
        selected = max(
            coverage_feasible or routing_grid,
            key=lambda row: float(row["selective_accuracy"]),
        )
    high_confidence = float(selected["confidence_threshold"])
    route_margin = float(selected["margin_threshold"])
    clarify_candidates = [
        row for row in curve if float(row["coverage"]) >= min(0.95, args.target_coverage + 0.15)
    ]
    clarify_threshold = (
        max(float(row["threshold"]) for row in clarify_candidates) if clarify_candidates else 0.0
    )
    payload = {
        "schema_version": "1.0",
        "data_version": "intent_v1",
        "calibration_report": args.calibration_report.as_posix(),
        "temperature": float(report["temperature"]),
        "auto_route_min_confidence": high_confidence,
        "route_margin": route_margin,
        "clarify_below_confidence": clarify_threshold,
        "target_coverage": args.target_coverage,
        "target_selective_accuracy": args.target_selective_accuracy,
        "observed_coverage": float(selected["coverage"]),
        "observed_selective_accuracy": float(selected["selective_accuracy"]),
        "target_met": target_met,
        "status": "verified_internal_validation" if target_met else "target_unmet",
        "selection_objective": (
            "maximize selective accuracy, then coverage, while meeting both targets"
        ),
        "note": "Thresholds use internal validation and must be revalidated on frozen human gold.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
