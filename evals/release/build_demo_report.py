from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED = {
    "ai_acceptance": Path("reports/eval/ai_assisted_acceptance_v1.json"),
    "intent": Path("reports/training/ai_assisted_evaluation_v1.json"),
    "routing": Path("reports/eval/routing_800_v1_ai_assisted.json"),
    "tool_selection": Path("reports/eval/tool_selection_1000_v1_ai_assisted.json"),
    "e2e_review": Path("reports/eval/e2e_360_v1_ai_assisted_review.json"),
    "load_mixed": Path("reports/eval/load_mixed.json"),
    "load_faq_cache": Path("reports/eval/load_faq_cache.json"),
    "security": Path("reports/eval/security_attacks_v1.json"),
    "readiness": Path("reports/release/readiness.json"),
    "smoke": Path("reports/release/smoke_demo.json"),
    "mysql_restore": Path("reports/release/mysql_backup_restore.json"),
    "checkpoint_backup": Path("reports/release/checkpoint_backup.json"),
    "docker_environment": Path("reports/release/docker_environment.json"),
    "restart_recovery": Path("reports/api/p7_smoke.json"),
    "browser_visual_qa": Path("reports/release/browser_visual_qa.json"),
}


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def git(root: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def build(root: Path) -> dict[str, Any]:
    evidence = {name: read_json(root / path) for name, path in REQUIRED.items()}
    blockers = [
        f"missing evidence: {REQUIRED[name]}" for name, value in evidence.items() if value is None
    ]
    expected = {
        "ai_acceptance": ("status", "ai_assisted_verified_for_demo"),
        "intent": ("evaluation_status", "ai_assisted_verified_for_demo"),
        "routing": ("suite_status", "ai_assisted_verified_for_demo"),
        "tool_selection": ("evaluation_status", "ai_assisted_verified_for_demo"),
        "e2e_review": ("status", "ai_assisted_verified_for_demo"),
        "load_mixed": ("evaluation_status", "verified"),
        "load_faq_cache": ("evaluation_status", "verified"),
        "security": ("evaluation_status", "verified"),
        "readiness": ("evaluation_status", "verified"),
        "smoke": ("evaluation_status", "verified"),
        "mysql_restore": ("evaluation_status", "verified"),
        "checkpoint_backup": ("evaluation_status", "verified"),
        "docker_environment": ("evaluation_status", "verified"),
        "restart_recovery": ("status", "passed"),
        "browser_visual_qa": ("evaluation_status", "ai_assisted_verified_for_demo"),
    }
    for name, (field, required_value) in expected.items():
        value = evidence[name]
        if value is not None and value.get(field) != required_value:
            blockers.append(f"{name}.{field} must be {required_value!r}; got {value.get(field)!r}")

    acceptance = evidence["ai_acceptance"] or {}
    human_statuses = {
        section: acceptance.get(section, {}).get("human_review_status")
        for section in ("intent", "routing", "tool_selection", "e2e")
    }
    if any(status != "not_performed" for status in human_statuses.values()):
        blockers.append(
            "AI-assisted evidence must explicitly retain human_review_status=not_performed"
        )

    commit = git(root, "rev-parse", "HEAD")
    porcelain = git(root, "status", "--porcelain")
    deepseek = read_json(root / "reports/agent/p6_deepseek_live.json") or {}
    waivers: list[dict[str, Any]] = [
        {
            "gate": "independent_human_review",
            "status": "waived_for_demo_only",
            "reason": "Project owner accepted AI-assisted labels for this demo scope.",
        },
        {
            "gate": "git_release_promotion",
            "status": "not_applicable_to_demo_evidence",
            "reason": "Strict latest_verified.json remains fail-closed and unchanged.",
        },
        {
            "gate": "fresh_clone_reproduction",
            "status": "not_performed_demo_only",
            "reason": (
                "The repository has no initial Git HEAD, so the measured run used the current "
                "workspace and newly recreated containers rather than a separate fresh clone."
            ),
        },
        {
            "gate": "demo_video",
            "status": "not_recorded",
            "reason": (
                "The ten-scenario executable smoke report is available; a presentation video "
                "is a separate manual delivery artifact."
            ),
        },
    ]
    if deepseek.get("status") != "passed":
        waivers.insert(
            1,
            {
                "gate": "deepseek_live_success",
                "status": "waived_for_demo_only",
                "observed_status": deepseek.get("status", "not_run"),
                "reason": "HTTP failures degrade safely; live model quality is not claimed.",
            },
        )
    return {
        "schema_version": "1.0",
        "release_profile": "ai_assisted_demo_non_production",
        "verification_status": "demo_verified_with_waivers" if not blockers else "blocked",
        "blockers": blockers,
        "provenance": "ai_assisted_project_owner_accepted",
        "human_review_status": "not_performed",
        "git_commit": commit,
        "working_tree_clean": porcelain == "" if porcelain is not None else False,
        "waivers": waivers,
        "deepseek_live": {
            "status": deepseek.get("status", "not_run"),
            "model": deepseek.get("model"),
            "checked_at": deepseek.get("checked_at"),
        },
        "evidence": {
            name: {
                "path": str(path),
                "status": (
                    value.get("evaluation_status", value.get("status", value.get("suite_status")))
                    if value is not None
                    else None
                ),
            }
            for (name, path), value in zip(REQUIRED.items(), evidence.values(), strict=True)
        },
        "metrics": {
            "intent": (evidence["intent"] or {}).get("metrics", evidence["intent"]),
            "routing": (evidence["routing"] or {}).get("metrics"),
            "tool_selection": (evidence["tool_selection"] or {}).get("metrics"),
            "e2e_ai_review": evidence["e2e_review"],
            "load_mixed": evidence["load_mixed"],
            "load_faq_cache": evidence["load_faq_cache"],
            "security": evidence["security"],
        },
        "created_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AI-assisted demo evidence report")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--candidate-output",
        type=Path,
        default=Path("reports/release/latest_demo_candidate.json"),
    )
    parser.add_argument(
        "--verified-output",
        type=Path,
        default=Path("reports/release/latest_demo_verified.json"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    report = build(root)
    candidate = root / args.candidate_output
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["verification_status"] == "demo_verified_with_waivers":
        verified = root / args.verified_output
        verified.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "verification_status": report["verification_status"],
                "blockers": report["blockers"],
                "candidate_output": str(candidate),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["blockers"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
