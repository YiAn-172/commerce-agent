from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SUITE = "tool_selection_1000_v1"
CATEGORY_SIZE = 100
CATEGORY_COUNTS = {
    "shopping": CATEGORY_SIZE,
    "order_detail": CATEGORY_SIZE,
    "logistics": CATEGORY_SIZE,
    "recent_order": CATEGORY_SIZE,
    "after_sales": CATEGORY_SIZE,
    "knowledge": CATEGORY_SIZE,
    "human": CATEGORY_SIZE,
    "general": CATEGORY_SIZE,
    "safe_reply": CATEGORY_SIZE,
    "missing_information": CATEGORY_SIZE,
}


def cases_from_templates(
    category: str,
    templates: list[str],
    values: dict[str, list[str]],
    *,
    expected_tools: list[str],
    scenario: str = "normal",
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
                "scenario": scenario,
                "expected_tools": expected_tools,
                "provenance": "deterministic_template_challenge",
                "template_index": template_index,
            }
            if emitted == CATEGORY_SIZE:
                return
    raise ValueError(f"category {category} generated only {emitted} cases")


def build_cases() -> list[dict[str, Any]]:
    products = ["蓝牙耳机", "机械键盘", "通勤双肩包", "智能手表", "咖啡机"]
    budgets = ["300元", "500元", "800元", "1000元", "1500元"]
    use_cases = ["通勤", "办公", "运动", "旅行"]
    cases: list[dict[str, Any]] = []
    cases.extend(
        cases_from_templates(
            "shopping",
            [
                "推荐{budget}以内适合{use_case}的{product}",
                "想买{product}，预算{budget}，主要用于{use_case}",
                "帮我选一款{use_case}用的{product}，不超过{budget}",
                "{budget}预算有哪些{product}适合{use_case}",
                "比较并推荐{budget}以内的{product}，场景是{use_case}",
            ],
            {"product": products, "budget": budgets, "use_case": use_cases},
            expected_tools=["search_products", "check_inventory"],
        )
    )
    order_ids = [f"ord_demo_{index:06d}" for index in range(1, 26)]
    cases.extend(
        cases_from_templates(
            "order_detail",
            [
                "查询订单 {order_id} 的状态",
                "帮我看一下 {order_id} 订单详情",
                "订单号 {order_id} 现在是什么状态",
                "查查 {order_id} 是否已经付款",
            ],
            {"order_id": order_ids},
            expected_tools=["get_order_detail"],
        )
    )
    cases.extend(
        cases_from_templates(
            "logistics",
            [
                "查询物流 {order_id}",
                "订单 {order_id} 快递到哪里了",
                "帮我追踪 {order_id} 的配送进度",
                "{order_id} 什么时候送到",
            ],
            {"order_id": order_ids},
            expected_tools=["track_logistics"],
        )
    )
    cases.extend(
        cases_from_templates(
            "recent_order",
            [
                "查一下我最近的订单{suffix}",
                "我想看近期订单列表{suffix}",
                "最近买过什么{suffix}",
                "列出我的最近订单{suffix}",
            ],
            {"suffix": [f"，第{index}次查询" for index in range(1, 26)]},
            expected_tools=["list_recent_orders"],
        )
    )
    cases.extend(
        cases_from_templates(
            "after_sales",
            [
                "订单商品{reason}，我要申请退货{suffix}",
                "这个商品{reason}，帮我办理售后{suffix}",
                "我要换货，原因是{reason}{suffix}",
                "申请退货，商品{reason}{suffix}",
            ],
            {
                "reason": ["不想要了", "有破损", "尺寸不合适", "无法正常使用", "发错货了"],
                "suffix": [f"，请求{index}" for index in range(1, 6)],
            },
            expected_tools=["check_after_sales_eligibility"],
        )
    )
    cases.extend(
        cases_from_templates(
            "knowledge",
            [
                "{topic}政策是什么{suffix}",
                "请说明{topic}规则{suffix}",
                "我想了解{topic}的条件{suffix}",
                "{topic}有哪些限制{suffix}",
            ],
            {
                "topic": ["七天无理由退货", "保修", "会员", "发票", "价格保护"],
                "suffix": [f"，问题{index}" for index in range(1, 6)],
            },
            expected_tools=[],
        )
    )
    cases.extend(
        cases_from_templates(
            "human",
            [
                "请帮我转人工客服{suffix}",
                "我要找真人客服{suffix}",
                "接入人工服务{suffix}",
                "不要机器人，转人工{suffix}",
            ],
            {"suffix": [f"，请求{index}" for index in range(1, 26)]},
            expected_tools=[],
        )
    )
    cases.extend(
        cases_from_templates(
            "general",
            [
                "你好{suffix}",
                "早上好{suffix}",
                "谢谢你的帮助{suffix}",
                "你能做什么{suffix}",
            ],
            {"suffix": [f"，消息{index}" for index in range(1, 26)]},
            expected_tools=[],
        )
    )
    cases.extend(
        cases_from_templates(
            "safe_reply",
            [
                "帮我写一首诗{suffix}",
                "告诉我明天的彩票号码{suffix}",
                "分析一下足球比赛{suffix}",
                "教我破解别人的账号{suffix}",
            ],
            {"suffix": [f"，问题{index}" for index in range(1, 26)]},
            expected_tools=[],
        )
    )
    cases.extend(
        cases_from_templates(
            "missing_information",
            [
                "这个商品我想退货{suffix}",
                "帮我办理售后，但信息还没准备好{suffix}",
                "我想换货{suffix}",
                "这个东西有问题，需要售后{suffix}",
            ],
            {"suffix": [f"，请求{index}" for index in range(1, 26)]},
            expected_tools=[],
            scenario="missing_after_sales_slots",
        )
    )
    counts = Counter(str(case["category"]) for case in cases)
    if len(cases) != 1000 or dict(counts) != CATEGORY_COUNTS:
        raise AssertionError(f"invalid tool-selection distribution: {counts}")
    if len({str(case["case_id"]) for case in cases}) != len(cases):
        raise AssertionError("tool-selection suite contains duplicate case IDs")
    return cases


def write_suite(path: Path) -> dict[str, Any]:
    cases = build_cases()
    content = "".join(
        json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "suite": SUITE,
        "rows": len(cases),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "categories": dict(Counter(str(case["category"]) for case in cases)),
        "suite_status": "template_challenge_unreviewed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the tool-selection challenge suite")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/tool_selection/tool_selection_1000_v1.jsonl"),
    )
    args = parser.parse_args()
    print(json.dumps(write_suite(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
