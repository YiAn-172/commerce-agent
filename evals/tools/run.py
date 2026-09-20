from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.business.demo_data import BASE_TIME, build_demo_dataset
from packages.business.models import ApprovalTask, Base, ServiceTicket
from packages.contracts.after_sales import (
    AfterSalesEligibilityInput,
    AfterSalesRequestType,
    PrefillServiceTicketInput,
)
from packages.contracts.catalog import (
    CheckInventoryInput,
    ProductDetailInput,
    SearchProductsInput,
)
from packages.contracts.common import ToolEnvelope
from packages.contracts.order import (
    GetOrderDetailInput,
    ListRecentOrdersInput,
    TrackLogisticsInput,
)
from packages.mcp_core.after_sales import AfterSalesToolService
from packages.mcp_core.catalog import CatalogToolService
from packages.mcp_core.context import TrustedToolContext
from packages.mcp_core.order import OrderToolService
from packages.mcp_core.runtime import execute_tool
from services.mcp_after_sales.server import mcp as after_sales_mcp
from services.mcp_catalog.server import mcp as catalog_mcp
from services.mcp_order.server import mcp as order_mcp

SUITES = {"tools_1000_v1": Path("evals/tools/tools_1000_v1.jsonl")}


def load_suite(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Tools suite is missing: {path}. Run evals.tools.build_suite first.")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    required = {"case_id", "category", "tool_name", "arguments", "provenance"}
    if len(cases) != 1000:
        raise SystemExit(f"tools_1000_v1 must contain exactly 1,000 cases; got {len(cases)}")
    if any(required - set(case) for case in cases):
        raise SystemExit("tools suite contains cases with missing required fields")
    if len({str(case["case_id"]) for case in cases}) != len(cases):
        raise SystemExit("tools suite contains duplicate case IDs")
    return cases


def context(case: dict[str, Any]) -> TrustedToolContext:
    suffix = str(case["case_id"]).replace("-", "_")
    return TrustedToolContext(
        principal_id=str(case.get("principal_id", "usr_demo_0001")),
        scopes=frozenset(str(value) for value in case.get("scopes", [])),
        audience="tools-evaluation",
        trace_id=f"tr_eval_{suffix}",
        request_id=f"req_eval_{suffix}",
        deadline_ms=10_000,
    )


async def exposed_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    servers: tuple[FastMCP[Any], ...] = (catalog_mcp, order_mcp, after_sales_mcp)
    for server in servers:
        for tool in await server.list_tools():
            schemas[tool.name] = tool.inputSchema
    return schemas


class ToolHarness:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.catalog = CatalogToolService(factory)
        self.order = OrderToolService(factory)
        self.after_sales = AfterSalesToolService(
            factory,
            clock=lambda: BASE_TIME + timedelta(days=1),
        )

    async def call(self, case: dict[str, Any]) -> ToolEnvelope[Any]:
        tool_name = str(case["tool_name"])
        arguments = dict(case["arguments"])
        trusted = context(case)
        operations: dict[str, Callable[[], Awaitable[ToolEnvelope[Any]]]] = {
            "get_product_detail": lambda: self.catalog.get_product_detail(
                trusted, ProductDetailInput.model_validate(arguments)
            ),
            "check_inventory": lambda: self.catalog.check_inventory(
                trusted, CheckInventoryInput.model_validate(arguments)
            ),
            "search_products": lambda: self.catalog.search_products(
                trusted, SearchProductsInput.model_validate(arguments)
            ),
            "get_order_detail": lambda: self.order.get_order_detail(
                trusted, GetOrderDetailInput.model_validate(arguments)
            ),
            "list_recent_orders": lambda: self.order.list_recent_orders(
                trusted, ListRecentOrdersInput.model_validate(arguments)
            ),
            "track_logistics": lambda: self.order.track_logistics(
                trusted, TrackLogisticsInput.model_validate(arguments)
            ),
            "check_after_sales_eligibility": lambda: self.after_sales.check_eligibility(
                trusted,
                AfterSalesEligibilityInput(
                    order_id=str(arguments["order_id"]),
                    item_id=str(arguments["item_id"]),
                    request_type=AfterSalesRequestType(str(arguments["request_type"])),
                    reason_code=str(arguments["reason_code"]),
                ),
            ),
            "prefill_service_ticket": lambda: self.after_sales.prefill_service_ticket(
                trusted,
                PrefillServiceTicketInput(
                    order_id=str(arguments["order_id"]),
                    item_id=str(arguments["item_id"]),
                    request_type=AfterSalesRequestType(str(arguments["request_type"])),
                    reason_code=str(arguments["reason_code"]),
                    description=str(arguments["description"]),
                    idempotency_key=str(arguments["idempotency_key"]),
                ),
            ),
        }
        return await operations[tool_name]()


async def initialize_database() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    dataset = build_demo_dataset()
    async with factory() as session, session.begin():
        for model, records in dataset.rows.items():
            for start in range(0, len(records), 1000):
                await session.execute(insert(model), records[start : start + 1000])
    return engine, factory


async def run_fault_case(case: dict[str, Any]) -> ToolEnvelope[Any]:
    trusted = context({**case, "scopes": ["order:read"]})
    if case["fault_kind"] == "timeout":

        async def operation() -> str:
            await asyncio.sleep(0.02)
            return "late"

        trusted = TrustedToolContext(
            principal_id=trusted.principal_id,
            scopes=trusted.scopes,
            audience=trusted.audience,
            trace_id=trusted.trace_id,
            request_id=trusted.request_id,
            deadline_ms=1,
        )
    else:

        async def operation() -> str:
            raise SQLAlchemyError("synthetic dependency failure")

    return await execute_tool(
        name="synthetic_read",
        context=trusted,
        required_scope="order:read",
        operation=operation,
    )


def result_row(case: dict[str, Any], passed: bool, **details: Any) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "tool_name": case["tool_name"],
        "passed": passed,
        **details,
    }


async def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    engine, factory = await initialize_database()
    harness = ToolHarness(factory)
    schemas = await exposed_schemas()
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            category = str(case["category"])
            if category == "schema_rejection":
                schema = schemas[str(case["tool_name"])]
                properties = set(schema.get("properties", {}))
                unexpected = sorted(set(case["arguments"]) - properties)
                rejects_extras = schema.get("additionalProperties") is False
                rows.append(
                    result_row(
                        case,
                        rejects_extras and bool(unexpected),
                        unexpected_properties=unexpected,
                        additional_properties=schema.get("additionalProperties"),
                    )
                )
                continue

            if category == "fault_handling":
                envelope = await run_fault_case(case)
            else:
                envelope = await harness.call(case)

            if category == "legal_request":
                passed = envelope.ok and envelope.data is not None and envelope.error is None
            elif category in {"permission_rejection", "ownership_isolation", "fault_handling"}:
                expected_error = str(case["expected_error"])
                passed = (
                    not envelope.ok
                    and envelope.data is None
                    and envelope.error is not None
                    and envelope.error.code.value == expected_error
                )
                if category == "ownership_isolation":
                    serialized = envelope.model_dump_json()
                    passed = passed and all(
                        str(field) not in serialized for field in case["forbidden_fields"]
                    )
            elif category == "idempotency":
                second = await harness.call(case)
                first_data = envelope.data
                second_data = second.data
                passed = bool(
                    envelope.ok
                    and second.ok
                    and first_data is not None
                    and second_data is not None
                    and first_data.ticket_id == second_data.ticket_id
                    and first_data.duplicate is False
                    and second_data.duplicate is True
                )
            else:
                raise AssertionError(f"unknown category: {category}")

            rows.append(
                result_row(
                    case,
                    passed,
                    ok=envelope.ok,
                    error_code=envelope.error.code.value if envelope.error else None,
                )
            )

        idempotency_keys = [
            str(case["arguments"]["idempotency_key"])
            for case in cases
            if case["category"] == "idempotency"
        ]
        async with factory() as session:
            ticket_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ServiceTicket)
                    .where(ServiceTicket.idempotency_key.in_(idempotency_keys))
                )
                or 0
            )
            approval_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ApprovalTask)
                    .join(ServiceTicket, ApprovalTask.ticket_id == ServiceTicket.id)
                    .where(ServiceTicket.idempotency_key.in_(idempotency_keys))
                )
                or 0
            )
        if ticket_count != 100 or approval_count != 100:
            for row in rows:
                if row["category"] == "idempotency":
                    row["passed"] = False
                    row["database_count_mismatch"] = {
                        "tickets": ticket_count,
                        "approvals": approval_count,
                    }
    finally:
        await engine.dispose()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["category"])].append(row)

    def metric(category: str) -> dict[str, Any]:
        selected = grouped[category]
        correct = sum(bool(row["passed"]) for row in selected)
        return {
            "correct": correct,
            "total": len(selected),
            "accuracy": correct / len(selected),
        }

    failures = [row for row in rows if not row["passed"]]
    legal = metric("legal_request")
    idempotency = metric("idempotency")
    return {
        "overall": {
            "correct": len(rows) - len(failures),
            "total": len(rows),
            "accuracy": (len(rows) - len(failures)) / len(rows),
        },
        "tool_selection_accuracy": {
            "status": "not_measured",
            "reason": "The suite begins after tool selection and evaluates executable contracts.",
        },
        "parameter_correctness_given_selected_tool": legal,
        "legal_request_success_rate": legal,
        "schema_rejection_rate": metric("schema_rejection"),
        "permission_rejection_rate": metric("permission_rejection"),
        "ownership_isolation_rate": metric("ownership_isolation"),
        "fault_handling_rate": metric("fault_handling"),
        "idempotency_rate": idempotency,
        "duplicate_business_writes": 100 - idempotency["correct"],
        "unauthorized_field_leaks": 100 - metric("ownership_isolation")["correct"],
        "by_category": {name: metric(name) for name in sorted(grouped)},
        "failure_count": len(failures),
        "failure_samples": failures[:100],
    }


async def main_async() -> None:
    logging.getLogger("commerce_agent.mcp").setLevel(logging.CRITICAL)
    parser = argparse.ArgumentParser(description="Run tools_1000_v1 against local services")
    parser.add_argument("--suite", choices=sorted(SUITES), default="tools_1000_v1")
    parser.add_argument("--report", type=Path, default=Path("reports/eval/tools_1000_v1.json"))
    args = parser.parse_args()
    suite_path = SUITES[args.suite]
    cases = load_suite(suite_path)
    metrics = await evaluate(cases)
    report = {
        "schema_version": "1.0",
        "suite_version": args.suite,
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "suite_status": "deterministic_demo_verified",
        "execution_backend": "in_memory_sqlite_with_production_tool_services",
        "metrics": metrics,
        "limitations": [
            "The dataset is deterministic demo data, not independently reviewed human gold.",
            "Tool selection is not measured because this suite starts after tool selection.",
            "Database portability is checked separately by the MySQL integration suite.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"report": str(args.report), **metrics["overall"]},
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
