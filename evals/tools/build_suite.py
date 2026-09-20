from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from packages.business.demo_data import build_demo_dataset
from packages.business.models import LogisticsEvent, Order, Product, Sku

SUITE_VERSION = "tools_1000_v1"
EXPECTED_COUNTS = {
    "legal_request": 400,
    "schema_rejection": 150,
    "permission_rejection": 150,
    "ownership_isolation": 100,
    "fault_handling": 100,
    "idempotency": 100,
}


def _case(
    case_id: str,
    category: str,
    tool_name: str,
    arguments: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "tool_name": tool_name,
        "arguments": arguments,
        "provenance": "deterministic_demo_contract",
        **extra,
    }


def build_cases() -> list[dict[str, Any]]:
    dataset = build_demo_dataset()
    products = sorted(dataset.rows[Product], key=lambda row: str(row["id"]))
    skus = sorted(dataset.rows[Sku], key=lambda row: str(row["id"]))
    orders = sorted(dataset.rows[Order], key=lambda row: str(row["id"]))
    logistics_order_ids = sorted({str(row["order_id"]) for row in dataset.rows[LogisticsEvent]})
    order_by_id = {str(row["id"]): row for row in orders}
    cases: list[dict[str, Any]] = []

    for index in range(50):
        product = products[index]
        product_id = str(product["id"])
        sku_number = index * 4 + 1
        sku_id = str(skus[sku_number - 1]["id"])
        query = str(product["name"])
        cases.extend(
            [
                _case(
                    f"legal_detail_{index + 1:03d}",
                    "legal_request",
                    "get_product_detail",
                    {"product_id": product_id},
                    principal_id="usr_demo_0001",
                    scopes=["catalog:read"],
                    expected="ok",
                ),
                _case(
                    f"legal_inventory_{index + 1:03d}",
                    "legal_request",
                    "check_inventory",
                    {"product_id": product_id, "sku_id": sku_id, "region": "华东"},
                    principal_id="usr_demo_0001",
                    scopes=["catalog:read"],
                    expected="ok",
                ),
                _case(
                    f"legal_search_{index + 1:03d}",
                    "legal_request",
                    "search_products",
                    {"query": query, "limit": 5},
                    principal_id="usr_demo_0001",
                    scopes=["catalog:read"],
                    expected="ok",
                ),
            ]
        )

    for index in range(50):
        order = orders[index]
        order_id = str(order["id"])
        principal_id = str(order["user_id"])
        cases.extend(
            [
                _case(
                    f"legal_order_{index + 1:03d}",
                    "legal_request",
                    "get_order_detail",
                    {"order_id": order_id},
                    principal_id=principal_id,
                    scopes=["order:read"],
                    expected="ok",
                ),
                _case(
                    f"legal_recent_{index + 1:03d}",
                    "legal_request",
                    "list_recent_orders",
                    {"limit": 3 + index % 3},
                    principal_id=f"usr_demo_{index + 1:04d}",
                    scopes=["order:read"],
                    expected="ok",
                ),
            ]
        )

    for index, order_id in enumerate(logistics_order_ids[:50], start=1):
        cases.append(
            _case(
                f"legal_logistics_{index:03d}",
                "legal_request",
                "track_logistics",
                {"order_id": order_id},
                principal_id=str(order_by_id[order_id]["user_id"]),
                scopes=["order:read"],
                expected="ok",
            )
        )

    for index in range(100):
        cases.append(
            _case(
                f"legal_eligibility_{index + 1:03d}",
                "legal_request",
                "check_after_sales_eligibility",
                {
                    "order_id": "ord_demo_000005",
                    "item_id": "item_demo_000005_1",
                    "request_type": "return",
                    "reason_code": f"EVAL_REASON_{index + 1:03d}",
                },
                principal_id="usr_demo_0005",
                scopes=["after-sales:read"],
                expected="ok",
            )
        )

    schema_tools: list[tuple[str, dict[str, Any]]] = [
        ("get_product_detail", {"product_id": "prd_demo_000001"}),
        (
            "check_inventory",
            {
                "product_id": "prd_demo_000001",
                "sku_id": "sku_demo_000001",
                "region": "华东",
            },
        ),
        ("search_products", {"query": "耳机", "limit": 5}),
        ("get_order_detail", {"order_id": "ord_demo_000001"}),
        ("list_recent_orders", {"limit": 3}),
        ("track_logistics", {"order_id": "ord_demo_000004"}),
        (
            "check_after_sales_eligibility",
            {
                "order_id": "ord_demo_000005",
                "item_id": "item_demo_000005_1",
                "request_type": "return",
                "reason_code": "DO_NOT_WANT",
            },
        ),
        (
            "prefill_service_ticket",
            {
                "order_id": "ord_demo_000005",
                "item_id": "item_demo_000005_1",
                "request_type": "return",
                "reason_code": "DO_NOT_WANT",
                "description": "schema evaluation",
                "idempotency_key": "eval_schema_00000001",
            },
        ),
    ]
    for index in range(150):
        tool_name, arguments = schema_tools[index % len(schema_tools)]
        invalid = {**arguments, f"untrusted_identity_{index:03d}": "usr_attacker"}
        cases.append(
            _case(
                f"schema_{index + 1:03d}",
                "schema_rejection",
                tool_name,
                invalid,
                expected="validation_error",
            )
        )

    permission_tools: list[tuple[str, dict[str, Any]]] = schema_tools
    for index in range(150):
        tool_name, arguments = permission_tools[index % len(permission_tools)]
        cases.append(
            _case(
                f"permission_{index + 1:03d}",
                "permission_rejection",
                tool_name,
                arguments,
                principal_id="usr_demo_0001",
                scopes=[],
                expected_error="PERMISSION_DENIED",
            )
        )

    for index, order in enumerate(orders[:100], start=1):
        owner_number = int(str(order["user_id"]).rsplit("_", 1)[1])
        attacker_number = owner_number % 100 + 1
        cases.append(
            _case(
                f"ownership_{index:03d}",
                "ownership_isolation",
                "get_order_detail",
                {"order_id": str(order["id"])},
                principal_id=f"usr_demo_{attacker_number:04d}",
                scopes=["order:read"],
                expected_error="NOT_FOUND",
                forbidden_fields=["total_amount", "items", "status"],
            )
        )

    for index in range(100):
        kind = "timeout" if index < 50 else "dependency_error"
        cases.append(
            _case(
                f"fault_{index + 1:03d}",
                "fault_handling",
                "synthetic_read",
                {},
                fault_kind=kind,
                expected_error="TIMEOUT" if kind == "timeout" else "DEPENDENCY_ERROR",
            )
        )

    for index in range(100):
        cases.append(
            _case(
                f"idempotency_{index + 1:03d}",
                "idempotency",
                "prefill_service_ticket",
                {
                    "order_id": "ord_demo_000005",
                    "item_id": "item_demo_000005_1",
                    "request_type": "return",
                    "reason_code": "DO_NOT_WANT",
                    "description": f"tools_1000 idempotency case {index + 1:03d}",
                    "idempotency_key": f"eval_tools_idem_{index + 1:08d}",
                },
                principal_id="usr_demo_0005",
                scopes=["after-sales:read", "after-sales:write"],
                expected="single_write",
            )
        )

    counts = Counter(str(case["category"]) for case in cases)
    if counts != Counter(EXPECTED_COUNTS):
        raise AssertionError(f"unexpected tools suite distribution: {dict(counts)}")
    if len(cases) != 1000 or len({str(case["case_id"]) for case in cases}) != 1000:
        raise AssertionError("tools suite must contain 1,000 unique case IDs")
    return cases


def write_suite(path: Path) -> dict[str, Any]:
    cases = build_cases()
    content = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "suite_version": SUITE_VERSION,
        "rows": len(cases),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "categories": dict(Counter(str(case["category"]) for case in cases)),
        "suite_status": "deterministic_demo_diagnostic",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the tools_1000_v1 diagnostic suite")
    parser.add_argument("--output", type=Path, default=Path("evals/tools/tools_1000_v1.jsonl"))
    args = parser.parse_args()
    print(json.dumps(write_suite(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
