from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CATEGORY_SIZE = 100
SUITE_SIZE = 800
SUITE_VERSION = "routing_800_v1"


def _cases(
    category: str,
    templates: list[str],
    values: dict[str, list[str]],
    *,
    expected_route: str | None,
    expected_decision: str,
) -> Iterable[dict[str, Any]]:
    keys = list(values)
    emitted = 0
    seen: set[str] = set()
    for combination in itertools.product(*(values[key] for key in keys)):
        slots = dict(zip(keys, combination, strict=True))
        for template_index, template in enumerate(templates):
            text = template.format(**slots)
            if text in seen:
                continue
            seen.add(text)
            emitted += 1
            yield {
                "case_id": f"{category}_{emitted:03d}",
                "category": category,
                "text": text,
                "previous_user_text": None,
                "expected_route": expected_route,
                "expected_decision": expected_decision,
                "provenance": "deterministic_template_challenge",
                "template_index": template_index,
            }
            if emitted == CATEGORY_SIZE:
                return
    raise ValueError(f"category {category} generated only {emitted} cases")


def build_cases() -> list[dict[str, Any]]:
    products = [
        "\u84dd\u7259\u8033\u673a",
        "\u673a\u68b0\u952e\u76d8",
        "\u901a\u52e4\u53cc\u80a9\u5305",
        "\u667a\u80fd\u624b\u8868",
        "\u7a7a\u6c14\u70b8\u9505",
    ]
    features = [
        "\u964d\u566a",
        "\u7eed\u822a",
        "\u91cd\u91cf",
        "\u4fdd\u4fee",
        "\u517c\u5bb9\u6027",
    ]
    budgets = ["300", "500", "800", "1200", "2000"]
    scenes = [
        "\u901a\u52e4",
        "\u529e\u516c",
        "\u65c5\u884c",
        "\u5bbf\u820d",
        "\u9001\u793c",
    ]
    order_ids = [f"ord_demo_{index:06d}" for index in range(1, 21)]
    reasons = [
        "\u4e0d\u60f3\u8981\u4e86",
        "\u5c3a\u5bf8\u4e0d\u5408\u9002",
        "\u6709\u8d28\u91cf\u95ee\u9898",
        "\u53d1\u9519\u8d27\u4e86",
        "\u5c11\u4e86\u914d\u4ef6",
    ]

    groups: list[list[dict[str, Any]]] = []
    groups.append(
        list(
            _cases(
                "product_consult",
                [
                    "\u5e2e\u6211\u67e5\u4e00\u4e0b{product}\u7684{feature}",
                    "{product}\u4e3b\u8981\u770b\u4ec0\u4e48\u53c2\u6570\uff0c\u5c24\u5176\u662f{feature}",
                    "\u8fd9\u6b3e{product}\u7684{feature}\u600e\u4e48\u6837",
                    "\u60f3\u4e86\u89e3{product}\uff0c\u8bf7\u8bf4\u660e{feature}",
                ],
                {"product": products, "feature": features},
                expected_route="knowledge",
                expected_decision="auto_route",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "product_recommend",
                [
                    "\u63a8\u8350{budget}\u5143\u4ee5\u5185\u9002\u5408{scene}\u7684{product}",
                    "\u9884\u7b97{budget}\uff0c\u60f3\u4e70{product}\u7528\u4e8e{scene}",
                    "\u5e2e\u6211\u9009\u4e00\u6b3e\u9002\u5408{scene}\u7684{product}\uff0c\u4e0d\u8d85\u8fc7{budget}\u5143",
                    "{scene}\u573a\u666f\u6709\u4ec0\u4e48{product}\u63a8\u8350\uff0c\u9884\u7b97{budget}",
                ],
                {"product": products, "budget": budgets, "scene": scenes},
                expected_route="shopping",
                expected_decision="auto_route",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "order_logistics",
                [
                    "\u67e5\u8be2\u8ba2\u5355{order_id}\u73b0\u5728\u5230\u54ea\u91cc\u4e86",
                    "\u8ba2\u5355{order_id}\u4ec0\u4e48\u65f6\u5019\u9001\u5230",
                    "\u5e2e\u6211\u770b\u4e0b{order_id}\u7684\u7269\u6d41\u8f68\u8ff9",
                    "{order_id}\u600e\u4e48\u8fd8\u6ca1\u6709\u66f4\u65b0\u7269\u6d41",
                    "\u8ba2\u5355{order_id}\u5f53\u524d\u662f\u4ec0\u4e48\u72b6\u6001",
                ],
                {"order_id": order_ids},
                expected_route="order",
                expected_decision="auto_route",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "after_sales",
                [
                    "\u8ba2\u5355{order_id}\u7684\u5546\u54c1{reason}\uff0c\u6211\u8981\u7533\u8bf7\u9000\u8d27",
                    "{order_id}\u6536\u5230\u540e\u53d1\u73b0{reason}\uff0c\u53ef\u4ee5\u6362\u8d27\u5417",
                    "\u5e2e\u6211\u5904\u7406{order_id}\uff0c\u539f\u56e0\u662f{reason}",
                    "\u8ba2\u5355{order_id}{reason}\uff0c\u552e\u540e\u600e\u4e48\u7533\u8bf7",
                    "{order_id}\u9700\u8981\u9000\u6b3e\uff0c{reason}",
                ],
                {"order_id": order_ids, "reason": reasons},
                expected_route="after_sales",
                expected_decision="auto_route",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "ambiguous",
                [
                    "\u8fd9\u4e2a{word}\u600e\u4e48\u5f04",
                    "\u521a\u624d\u90a3\u4e2a{word}\u5e2e\u6211\u770b\u4e00\u4e0b",
                    "\u80fd\u4e0d\u80fd\u5904\u7406\u4e00\u4e0b{word}",
                    "\u6211\u60f3\u95ee\u95ee{word}\u7684\u4e8b\u60c5",
                    "{word}\u600e\u4e48\u529e",
                ],
                {
                    "word": [
                        "\u4e1c\u897f",
                        "\u95ee\u9898",
                        "\u60c5\u51b5",
                        "\u4e8b\u60c5",
                        "\u90a3\u4e2a",
                        "\u5b83",
                        "\u8fd9\u5355",
                        "\u8fd9\u6b3e",
                        "\u8fdb\u5ea6",
                        "\u7533\u8bf7",
                        "\u7ed3\u679c",
                        "\u9875\u9762",
                        "\u8bb0\u5f55",
                        "\u72b6\u6001",
                        "\u5185\u5bb9",
                        "\u9009\u9879",
                        "\u6d88\u606f",
                        "\u64cd\u4f5c",
                        "\u670d\u52a1",
                        "\u529f\u80fd",
                    ]
                },
                expected_route="safe_reply",
                expected_decision="safe_reply",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "direct_dispatch",
                [
                    "{product}\u7684{policy}\u653f\u7b56\u662f\u4ec0\u4e48",
                    "\u8d2d\u4e70{product}\u540e\uff0c{policy}\u89c4\u5219\u600e\u4e48\u89c4\u5b9a",
                    "\u8bf7\u76f4\u63a5\u8bf4\u660e{product}\u9002\u7528\u7684{policy}\u89c4\u5219",
                    "{product}\u552e\u540e\u91cc\u7684{policy}\u6761\u6b3e\u662f\u4ec0\u4e48",
                ],
                {
                    "product": products,
                    "policy": [
                        "\u4e03\u5929\u65e0\u7406\u7531",
                        "\u4fdd\u4fee",
                        "\u53d1\u7968",
                        "\u6362\u8d27",
                        "\u8fd0\u8d39",
                    ],
                },
                expected_route="knowledge",
                expected_decision="auto_route",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "clarification",
                [
                    "\u6211\u60f3\u67e5{order_id}\u7684\u7269\u6d41\uff0c\u540c\u65f6\u63a8\u8350{budget}\u5143\u4ee5\u5185\u7684{product}",
                    "\u5148\u770b\u8ba2\u5355{order_id}\uff0c\u53e6\u5916{product}\u80fd\u4e0d\u80fd\u9000\u8d27",
                    "\u63a8\u8350{product}\uff0c\u7136\u540e\u5e2e\u6211\u5904\u7406\u8ba2\u5355{order_id}\u7684\u9000\u6b3e",
                    "\u67e5{order_id}\u5e76\u4e14\u8bf4\u660e{product}\u7684\u4fdd\u4fee\u653f\u7b56",
                ],
                {"order_id": order_ids, "budget": budgets, "product": products},
                expected_route=None,
                expected_decision="clarify",
            )
        )
    )
    groups.append(
        list(
            _cases(
                "human_handoff",
                [
                    "\u8bf7\u8f6c\u4eba\u5de5\u5ba2\u670d\u5904\u7406{topic}",
                    "\u6211\u8981\u4eba\u5de5\u5ba2\u670d\uff0c\u4e0d\u8981\u673a\u5668\u4eba\uff0c\u95ee\u9898\u662f{topic}",
                    "\u9a6c\u4e0a\u5e2e\u6211\u63a5\u4eba\u5de5\uff0c{topic}",
                    "\u8fd9\u4e2a\u95ee\u9898\u8bf7\u4eba\u5de5\u4ecb\u5165\uff1a{topic}",
                    "\u8f6c\u4eba\u5de5\uff0c\u6211\u9700\u8981\u5904\u7406{topic}",
                ],
                {
                    "topic": [
                        "\u8ba2\u5355\u5f02\u5e38",
                        "\u9000\u6b3e\u4e89\u8bae",
                        "\u5546\u54c1\u6295\u8bc9",
                        "\u7269\u6d41\u4e22\u5931",
                        "\u552e\u540e\u7533\u8bc9",
                        "\u4ef7\u683c\u95ee\u9898",
                        "\u53d1\u7968\u95ee\u9898",
                        "\u8d26\u53f7\u95ee\u9898",
                        "\u91cd\u590d\u6263\u6b3e",
                        "\u670d\u52a1\u6295\u8bc9",
                        "\u6362\u8d27\u5931\u8d25",
                        "\u4f18\u60e0\u4e89\u8bae",
                        "\u914d\u9001\u8d85\u65f6",
                        "\u5546\u54c1\u7834\u635f",
                        "\u4fdd\u4fee\u4e89\u8bae",
                        "\u8ba2\u5355\u53d6\u6d88",
                        "\u9000\u6b3e\u8d85\u65f6",
                        "\u5c11\u53d1\u5546\u54c1",
                        "\u9519\u53d1\u5546\u54c1",
                        "\u5ba2\u670d\u6295\u8bc9",
                    ]
                },
                expected_route="human",
                expected_decision="auto_route",
            )
        )
    )

    cases = [case for group in groups for case in group]
    if len(cases) != SUITE_SIZE:
        raise AssertionError(f"expected {SUITE_SIZE} cases, generated {len(cases)}")
    texts = [str(case["text"]) for case in cases]
    if len(set(texts)) != len(texts):
        duplicates = sorted(text for text, count in Counter(texts).items() if count > 1)
        raise AssertionError(f"routing suite contains duplicate texts: {duplicates[:10]}")
    return cases


def write_suite(path: Path) -> dict[str, Any]:
    cases = build_cases()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases)
    path.write_text(content, encoding="utf-8")
    categories: dict[str, int] = {}
    for case in cases:
        category = str(case["category"])
        categories[category] = categories.get(category, 0) + 1
    return {
        "suite_version": SUITE_VERSION,
        "rows": len(cases),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "categories": categories,
        "status": "template_challenge_unreviewed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the frozen routing challenge suite")
    parser.add_argument("--output", type=Path, default=Path("evals/routing/routing_800_v1.jsonl"))
    args = parser.parse_args()
    print(json.dumps(write_suite(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
