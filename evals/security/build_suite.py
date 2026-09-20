from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

SUITE = "attacks_v1"
CATEGORY_COUNTS = {
    "cross_user_order": 8,
    "identity_prompt_injection": 8,
    "malicious_knowledge_instruction": 8,
    "forged_approval_id": 8,
    "tool_html_payload": 8,
    "concurrent_idempotency": 8,
    "expired_jwt_replay": 8,
    "path_traversal_and_budget": 8,
}


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for category, count in CATEGORY_COUNTS.items():
        for index in range(1, count + 1):
            cases.append(
                {
                    "case_id": f"{category}_{index:03d}",
                    "category": category,
                    "index": index,
                    "provenance": "deterministic_security_contract",
                }
            )
    counts = Counter(str(case["category"]) for case in cases)
    if len(cases) < 50 or dict(counts) != CATEGORY_COUNTS:
        raise AssertionError(f"invalid attack-suite distribution: {counts}")
    return cases


def write_suite(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n"
            for case in build_cases()
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the security attack suite")
    parser.add_argument("--output", type=Path, default=Path("evals/security/attacks_v1.jsonl"))
    args = parser.parse_args()
    write_suite(args.output)
    print(json.dumps({"suite": SUITE, "cases": len(build_cases()), "output": str(args.output)}))


if __name__ == "__main__":
    main()
