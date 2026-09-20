from __future__ import annotations

import argparse
import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business.database import create_engine, session_factory
from packages.business.demo_data import EXPECTED_COUNTS
from packages.business.models import (
    REQUIRED_TABLE_NAMES,
    AfterSalesRule,
    ApprovalTask,
    Inventory,
    KnowledgeVersion,
    LogisticsEvent,
    Order,
    OrderItem,
    Product,
    ServiceTicket,
    Sku,
    User,
)
from packages.contracts.order import OrderStatus

FICTIONAL_BRANDS = {
    "星穹",
    "沐声",
    "岚芯",
    "远川",
    "微澜",
    "青禾",
    "曜石",
    "云雀",
    "新屿",
    "澄光",
}
MASKED_PHONE = re.compile(r"^13\d\*{4}\d{4}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate deterministic demo business data")
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/manifests/demo_business_v1.json")
    )
    parser.add_argument("--report", type=Path, default=Path("reports/data/demo_business_v1.json"))
    return parser.parse_args()


async def count(session: AsyncSession, model: type[Any]) -> int:
    value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def validate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.manifest.exists():
        raise RuntimeError(f"Seed manifest is missing: {args.manifest}")
    seed_manifest: dict[str, Any] = json.loads(args.manifest.read_text(encoding="utf-8"))
    engine = create_engine()
    async with engine.connect() as connection:
        table_names = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        ticket_unique_constraints = await connection.run_sync(
            lambda sync: inspect(sync).get_unique_constraints("service_tickets")
        )
    missing_tables = sorted(REQUIRED_TABLE_NAMES - table_names)
    ticket_unique_constraint_names = {
        str(constraint["name"])
        for constraint in ticket_unique_constraints
        if constraint.get("name")
    }
    required_ticket_unique_constraints = {
        "uq_ticket_principal_idem",
        "uq_ticket_fingerprint",
    }
    errors: list[str] = []
    if missing_tables:
        errors.append(f"missing required tables: {missing_tables}")
    missing_ticket_constraints = sorted(
        required_ticket_unique_constraints - ticket_unique_constraint_names
    )
    if missing_ticket_constraints:
        errors.append(f"missing service ticket unique constraints: {missing_ticket_constraints}")

    factory = session_factory(engine)
    async with factory() as session:
        models = [
            User,
            Product,
            Sku,
            Inventory,
            Order,
            OrderItem,
            LogisticsEvent,
            AfterSalesRule,
            ServiceTicket,
            ApprovalTask,
            KnowledgeVersion,
        ]
        counts = {model.__tablename__: await count(session, model) for model in models}
        seeded_runtime_counts = {
            "service_tickets": int(
                await session.scalar(
                    select(func.count())
                    .select_from(ServiceTicket)
                    .where(ServiceTicket.id.like("ticket_demo_%"))
                )
                or 0
            ),
            "approval_tasks": int(
                await session.scalar(
                    select(func.count())
                    .select_from(ApprovalTask)
                    .where(ApprovalTask.id.like("approval_demo_%"))
                )
                or 0
            ),
        }
        validation_counts = {**counts, **seeded_runtime_counts}
        for name, expected in EXPECTED_COUNTS.items():
            if name == "knowledge_versions":
                if int(counts.get(name, 0)) < expected:
                    errors.append(f"{name}: expected at least {expected}, got {counts.get(name)}")
            elif validation_counts.get(name) != expected:
                errors.append(
                    f"{name}: expected {expected} seeded rows, "
                    f"got {validation_counts.get(name)}"
                )
        for name, expected in seed_manifest.get("counts", {}).items():
            if name == "knowledge_versions":
                if int(counts.get(name, 0)) < expected:
                    errors.append(
                        f"{name}: seed manifest expected at least {expected}, "
                        f"got {counts.get(name)}"
                    )
            elif validation_counts.get(name) != expected:
                errors.append(
                    f"{name}: seed manifest expected {expected} seeded rows, "
                    f"got {validation_counts.get(name)}"
                )

        brands = set((await session.scalars(select(Product.brand).distinct())).all())
        if brands != FICTIONAL_BRANDS:
            errors.append(f"unexpected brand set: {sorted(brands)}")
        masked_phones = (await session.scalars(select(User.masked_phone))).all()
        invalid_phone_count = sum(not MASKED_PHONE.fullmatch(value) for value in masked_phones)
        if invalid_phone_count:
            errors.append(f"{invalid_phone_count} user phones are not masked")

        invalid_price_count = int(
            await session.scalar(select(func.count()).select_from(Sku).where(Sku.current_price < 0))
            or 0
        )
        invalid_inventory_count = int(
            await session.scalar(
                select(func.count()).select_from(Inventory).where(Inventory.available_quantity < 0)
            )
            or 0
        )
        if invalid_price_count or invalid_inventory_count:
            errors.append(
                f"invalid prices={invalid_price_count}, invalid inventory={invalid_inventory_count}"
            )

        item_totals = (
            await session.execute(
                select(
                    OrderItem.order_id,
                    func.sum(OrderItem.subtotal_snapshot),
                ).group_by(OrderItem.order_id)
            )
        ).all()
        item_total_map = {order_id: Decimal(str(total)) for order_id, total in item_totals}
        orders = (await session.scalars(select(Order))).all()
        amount_mismatches = sum(
            Decimal(str(order.total_amount)) != item_total_map.get(order.id) for order in orders
        )
        if amount_mismatches:
            errors.append(f"{amount_mismatches} order totals do not match item snapshots")

        event_count_rows = (
            await session.execute(
                select(LogisticsEvent.order_id, func.count()).group_by(LogisticsEvent.order_id)
            )
        ).all()
        event_counts: dict[str, int] = {
            str(order_id): int(event_count) for order_id, event_count in event_count_rows
        }
        shipped_like = {
            OrderStatus.SHIPPED.value,
            OrderStatus.DELIVERED.value,
            OrderStatus.AFTER_SALES_REQUESTED.value,
            OrderStatus.AFTER_SALES_PROCESSING.value,
            OrderStatus.REFUNDED.value,
            OrderStatus.CLOSED.value,
        }
        invalid_event_orders = sum(
            not (2 <= int(event_counts.get(order.id, 0)) <= 6)
            for order in orders
            if order.status in shipped_like
        )
        unexpected_event_orders = sum(
            order.id in event_counts for order in orders if order.status not in shipped_like
        )
        if invalid_event_orders or unexpected_event_orders:
            errors.append(
                "logistics cardinality failed: "
                f"invalid shipped={invalid_event_orders}, unexpected={unexpected_event_orders}"
            )

        ticket_rows = (
            await session.execute(
                select(ServiceTicket.refund_amount_snapshot, OrderItem.subtotal_snapshot).join(
                    OrderItem, OrderItem.id == ServiceTicket.item_id
                )
            )
        ).all()
        ticket_amount_mismatches = sum(
            Decimal(str(ticket_amount)) != Decimal(str(item_amount))
            for ticket_amount, item_amount in ticket_rows
        )
        if ticket_amount_mismatches:
            errors.append(
                f"{ticket_amount_mismatches} ticket refund snapshots differ from order items"
            )
        non_mock_approval_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ApprovalTask)
                .where(~ApprovalTask.action_type.startswith("mock_"))
            )
            or 0
        )
        if non_mock_approval_count:
            errors.append(f"{non_mock_approval_count} approval actions are not mock-only")
        enabled_rule_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AfterSalesRule)
                .where(AfterSalesRule.enabled.is_(True))
            )
            or 0
        )
        if enabled_rule_count != 20:
            errors.append(f"expected 20 active rules, got {enabled_rule_count}")

        scenario_ids = seed_manifest.get("scenario_ids", {})
        if len(scenario_ids) != 10:
            errors.append(f"expected exactly 10 stable demo scenarios, got {len(scenario_ids)}")
        order_scenarios = [value for value in scenario_ids.values() if value.startswith("ord_")]
        product_scenarios = [value for value in scenario_ids.values() if value.startswith("prd_")]
        inventory_scenarios = [value for value in scenario_ids.values() if value.startswith("inv_")]
        existing_orders = set(
            (await session.scalars(select(Order.id).where(Order.id.in_(order_scenarios)))).all()
        )
        existing_products = set(
            (
                await session.scalars(select(Product.id).where(Product.id.in_(product_scenarios)))
            ).all()
        )
        existing_inventory = set(
            (
                await session.scalars(
                    select(Inventory.id).where(Inventory.id.in_(inventory_scenarios))
                )
            ).all()
        )
        missing_scenarios = sorted(
            set(order_scenarios) - existing_orders
            | set(product_scenarios) - existing_products
            | set(inventory_scenarios) - existing_inventory
        )
        if missing_scenarios:
            errors.append(f"missing demo scenario records: {missing_scenarios}")

    await engine.dispose()
    report = {
        "schema_version": "1.0",
        "status": "passed" if not errors else "failed",
        "seed": seed_manifest.get("seed"),
        "data_version": seed_manifest.get("data_version"),
        "dataset_fingerprint": seed_manifest.get("dataset_fingerprint"),
        "required_table_count": len(REQUIRED_TABLE_NAMES),
        "missing_tables": missing_tables,
        "service_ticket_unique_constraints": sorted(ticket_unique_constraint_names),
        "missing_service_ticket_unique_constraints": missing_ticket_constraints,
        "counts": counts,
        "seeded_runtime_counts": seeded_runtime_counts,
        "logistics_event_rows": counts["logistics_events"],
        "order_total_mismatches": amount_mismatches,
        "ticket_amount_mismatches": ticket_amount_mismatches,
        "invalid_masked_phones": invalid_phone_count,
        "non_mock_approval_actions": non_mock_approval_count,
        "active_rule_count": enabled_rule_count,
        "scenario_ids": scenario_ids,
        "errors": errors,
    }
    if errors:
        raise RuntimeError("Demo data validation failed:\n- " + "\n- ".join(errors))
    return report


def main() -> None:
    args = parse_args()
    report = asyncio.run(validate(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
