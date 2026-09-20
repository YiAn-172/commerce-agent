from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.agent_core.graph import GRAPH_VERSION

REQUIRED_REPORTS = {
    "intent_gold": Path("reports/training/gold_evaluation_v1.json"),
    "routing": Path("reports/eval/routing_800_v1.json"),
    "routing_review": Path("reports/eval/routing_800_v1_review_summary.json"),
    "tools": Path("reports/eval/tools_1000_v1.json"),
    "tool_selection": Path("reports/eval/tool_selection_1000_v1.json"),
    "tool_selection_review": Path(
        "reports/eval/tool_selection_1000_v1_review_summary.json"
    ),
    "e2e": Path("reports/eval/e2e_360_v1.json"),
    "load_mixed": Path("reports/eval/load_mixed.json"),
    "load_faq_cache": Path("reports/eval/load_faq_cache.json"),
    "security": Path("reports/eval/security_attacks_v1.json"),
}


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def git_value(root: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def report_blockers(reports: dict[str, dict[str, Any] | None]) -> list[str]:
    blockers = [
        f"missing report: {path}"
        for name, path in REQUIRED_REPORTS.items()
        if reports.get(name) is None
    ]
    intent = reports.get("intent_gold")
    if intent is not None and intent.get("evaluation_status") != "human_gold_verified":
        blockers.append("intent gold report is not human_gold_verified")
    routing_review = reports.get("routing_review")
    if (
        routing_review is not None
        and routing_review.get("status") != "human_gold_verified"
    ):
        blockers.append("routing review summary is not human_gold_verified")
    routing = reports.get("routing")
    if routing is not None and routing.get("suite_status") != "human_gold_verified":
        blockers.append("routing suite is not independently reviewed human gold")
    if (
        routing is not None
        and routing_review is not None
        and routing.get("suite_status") == "human_gold_verified"
        and routing_review.get("status") == "human_gold_verified"
    ):
        if routing.get("suite_sha256") != routing_review.get("adjudicated_sha256"):
            blockers.append("routing report does not match the adjudicated suite hash")
        if routing.get("source_suite_sha256") != routing_review.get("source_sha256"):
            blockers.append("routing report does not match the reviewed source suite hash")
    tools = reports.get("tools")
    if tools is not None and tools.get("suite_status") not in {
        "human_gold_verified",
        "deterministic_demo_verified",
    }:
        blockers.append("tools suite is not verified")
    tool_selection_review = reports.get("tool_selection_review")
    if (
        tool_selection_review is not None
        and tool_selection_review.get("status") != "human_gold_verified"
    ):
        blockers.append("tool-selection review summary is not human_gold_verified")
    tool_selection = reports.get("tool_selection")
    if tool_selection is not None:
        if tool_selection.get("evaluation_status") != "human_gold_verified":
            blockers.append("tool-selection suite is not independently reviewed human gold")
        metric = tool_selection.get("metrics", {}).get("tool_selection_accuracy", {})
        if not isinstance(metric.get("accuracy"), (int, float)):
            blockers.append("tool-selection accuracy is missing")
    if (
        tool_selection is not None
        and tool_selection_review is not None
        and tool_selection.get("evaluation_status") == "human_gold_verified"
        and tool_selection_review.get("status") == "human_gold_verified"
    ):
        if (
            tool_selection.get("suite_sha256")
            != tool_selection_review.get("adjudicated_sha256")
        ):
            blockers.append(
                "tool-selection report does not match the adjudicated suite hash"
            )
        if (
            tool_selection.get("source_suite_sha256")
            != tool_selection_review.get("source_sha256")
        ):
            blockers.append(
                "tool-selection report does not match the reviewed source suite hash"
            )
    e2e = reports.get("e2e")
    if e2e is not None:
        if e2e.get("suite_status") != "deterministic_graph_contract_verified":
            blockers.append("end-to-end deterministic graph contract is not verified")
        judge = e2e.get("judge", {})
        if judge.get("status") != "verified":
            blockers.append("DeepSeek expression-quality Judge is not verified")
        manual = e2e.get("manual_review", {})
        if manual.get("status") != "human_review_verified":
            blockers.append("end-to-end 20% manual review is not complete")
    for name in ("load_mixed", "load_faq_cache"):
        value = reports.get(name)
        if value is not None and value.get("evaluation_status") != "verified":
            blockers.append(f"{name} report is not verified")
    security = reports.get("security")
    if security is not None and security.get("evaluation_status") != "verified":
        blockers.append("security attack-suite exit gates are not verified")
    return blockers


def build_candidate(root: Path) -> dict[str, Any]:
    reports = {name: read_json(root / path) for name, path in REQUIRED_REPORTS.items()}
    commit = git_value(root, "rev-parse", "HEAD")
    porcelain = git_value(root, "status", "--porcelain")
    working_tree_clean = porcelain == "" if porcelain is not None else False
    blockers = report_blockers(reports)
    if commit is None:
        blockers.append(
            "git HEAD is unavailable; create the first commit before release verification"
        )
    if not working_tree_clean:
        blockers.append("working tree is not clean")

    intent_manifest = read_json(root / "data/manifests/intent_v1.json") or {}
    model_manifest = read_json(root / "models/intent_classifier/current/model_manifest.json") or {}
    knowledge_manifests = sorted((root / "data/manifests").glob("kb_*.json"))
    latest_knowledge = read_json(knowledge_manifests[-1]) if knowledge_manifests else {}
    latest_knowledge = latest_knowledge or {}

    metrics = {
        name: value.get("metrics", value) if value is not None else None
        for name, value in reports.items()
    }
    return {
        "schema_version": "1.0",
        "verification_status": "verified" if not blockers else "blocked",
        "blockers": blockers,
        "git_commit": commit,
        "working_tree_clean": working_tree_clean,
        "data_version": intent_manifest.get("data_version"),
        "gold_version": (
            reports["intent_gold"].get("gold_version")
            if reports["intent_gold"] is not None
            else "intent_gold_v1"
        ),
        "intent_model": model_manifest.get("model_version"),
        "deepseek_model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "graph_version": GRAPH_VERSION,
        "tool_schema_version": "1.0",
        "knowledge_version": latest_knowledge.get("knowledge_version"),
        "metrics": metrics,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "created_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build fail-closed release evidence")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--candidate-output",
        type=Path,
        default=Path("reports/release/latest_candidate.json"),
    )
    parser.add_argument(
        "--verified-output",
        type=Path,
        default=Path("reports/release/latest_verified.json"),
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    candidate = build_candidate(root)
    candidate_output = root / args.candidate_output
    candidate_output.parent.mkdir(parents=True, exist_ok=True)
    candidate_output.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.promote:
        if candidate["verification_status"] != "verified":
            print(json.dumps(candidate, ensure_ascii=False, indent=2))
            raise SystemExit("release promotion blocked; latest_verified.json was not written")
        verified_output = root / args.verified_output
        verified_output.parent.mkdir(parents=True, exist_ok=True)
        verified_output.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "verification_status": candidate["verification_status"],
                "blockers": candidate["blockers"],
                "candidate_output": str(candidate_output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
