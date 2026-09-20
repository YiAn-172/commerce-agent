from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from packages.business.models import (
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
from packages.business.rules import idempotency_fingerprint, refund_amount_snapshot
from packages.contracts.after_sales import AfterSalesRequestType
from packages.contracts.order import OrderStatus

BASE_TIME = datetime(2026, 9, 15, 4, 0, 0)
DATA_VERSION = "demo-business-v1"
EXPECTED_COUNTS = {
    "users": 100,
    "products": 300,
    "skus": 1200,
    "inventory": 3600,
    "orders": 3000,
    "after_sales_rules": 80,
    "service_tickets": 100,
    "approval_tasks": 100,
    "knowledge_versions": 1,
}


@dataclass(frozen=True)
class DemoDataset:
    rows: dict[type[Any], list[dict[str, Any]]]
    scenario_ids: dict[str, str]
    fingerprint: str


def _fingerprint(rows: dict[type[Any], list[dict[str, Any]]]) -> str:
    digest = hashlib.sha256()
    for model, records in rows.items():
        digest.update(model.__tablename__.encode())
        digest.update(b"\0")
        digest.update(
            json.dumps(
                records,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def build_demo_dataset(seed: int = 20260915) -> DemoDataset:
    rng = random.Random(seed)
    categories = ["headphones", "phones", "tablets", "laptops", "accessories"]
    category_names = ["耳机", "手机", "平板", "笔记本", "数码配件"]
    brands = ["星穹", "沐声", "岚芯", "远川", "微澜", "青禾", "曜石", "云雀", "新屿", "澄光"]
    colors = ["曜石黑", "云雾白", "远山蓝", "晨曦金"]
    regions = ["华东", "华南", "华北"]
    warehouses = ["云仓一号", "云仓二号", "云仓三号"]

    users: list[dict[str, Any]] = []
    for index in range(1, 101):
        users.append(
            {
                "id": f"usr_demo_{index:04d}",
                "external_ref": f"demo-subject-{index:04d}",
                "display_name": f"演示用户{index:03d}",
                "masked_phone": f"13{index % 10}****{index:04d}",
                "region": regions[(index - 1) % len(regions)],
                "created_at": BASE_TIME - timedelta(days=180 - index),
            }
        )

    products: list[dict[str, Any]] = []
    skus: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    sku_prices: dict[str, Decimal] = {}
    sku_products: dict[str, str] = {}
    sku_names: dict[str, str] = {}
    for product_index in range(1, 301):
        category_index = (product_index - 1) % len(categories)
        category = categories[category_index]
        brand = brands[(product_index - 1) % len(brands)]
        product_id = f"prd_demo_{product_index:06d}"
        product_name = f"{brand}{category_names[category_index]} {product_index:03d} 型"
        products.append(
            {
                "id": product_id,
                "name": product_name,
                "brand": brand,
                "category": category,
                "status": "active",
                "description": "用于 CommerceAgent 演示的虚构 3C 商品，不对应真实品牌或型号。",
                "attributes_json": {
                    "wireless": product_index % 2 == 0,
                    "warranty_months": 12 + (product_index % 3) * 6,
                    "generation": 1 + product_index % 5,
                },
                "usage_tags": ["通勤", "办公"] if product_index % 2 == 0 else ["学习", "居家"],
                "audience_tags": ["学生"] if product_index % 3 == 0 else ["职场人"],
                "rating": Decimal("3.80") + Decimal(product_index % 12) / Decimal("10"),
                "sales_count": 100 + product_index * 17,
                "data_version": DATA_VERSION,
                "created_at": BASE_TIME - timedelta(days=365),
                "updated_at": BASE_TIME,
            }
        )
        for variant in range(4):
            sku_number = (product_index - 1) * 4 + variant + 1
            sku_id = f"sku_demo_{sku_number:06d}"
            price = (
                Decimal("199.00")
                + Decimal(category_index * 800)
                + Decimal(product_index % 50) * Decimal("23.00")
                + Decimal(variant * 40)
            )
            sku_name = f"{product_name} {colors[variant]}"
            sku_prices[sku_id] = price
            sku_products[sku_id] = product_id
            sku_names[sku_id] = sku_name
            skus.append(
                {
                    "id": sku_id,
                    "product_id": product_id,
                    "sku_code": f"DEMO-{sku_number:06d}",
                    "name": sku_name,
                    "color": colors[variant],
                    "specifications_json": {
                        "storage": f"{128 * (variant + 1)}GB",
                        "variant": variant + 1,
                    },
                    "current_price": price,
                    "price_snapshot_id": f"ps_demo_{sku_number:06d}",
                    "price_valid_until": BASE_TIME + timedelta(days=30),
                    "status": "active",
                    "created_at": BASE_TIME - timedelta(days=180),
                    "updated_at": BASE_TIME,
                }
            )
            for region_index, region in enumerate(regions):
                inventory.append(
                    {
                        "id": f"inv_demo_{sku_number:06d}_{region_index}",
                        "sku_id": sku_id,
                        "region": region,
                        "warehouse": warehouses[region_index],
                        "available_quantity": (sku_number * (region_index + 3)) % 121,
                        "as_of": BASE_TIME,
                    }
                )

    status_cycle = list(OrderStatus)
    shipped_statuses = {
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.AFTER_SALES_REQUESTED,
        OrderStatus.AFTER_SALES_PROCESSING,
        OrderStatus.REFUNDED,
        OrderStatus.CLOSED,
    }
    delivered_statuses = shipped_statuses - {OrderStatus.SHIPPED}
    orders: list[dict[str, Any]] = []
    order_items: list[dict[str, Any]] = []
    logistics_events: list[dict[str, Any]] = []
    scenario_ids: dict[str, str] = {}
    first_item_by_order: dict[str, dict[str, Any]] = {}
    for order_index in range(1, 3001):
        status = status_cycle[(order_index - 1) % len(status_cycle)]
        order_id = f"ord_demo_{order_index:06d}"
        scenario_ids.setdefault(status.value, order_id)
        user_id = f"usr_demo_{((order_index - 1) % 100) + 1:04d}"
        created_at = BASE_TIME - timedelta(days=order_index % 120, hours=order_index % 20)
        paid_at = (
            created_at + timedelta(minutes=15)
            if status not in {OrderStatus.PENDING_PAYMENT}
            else None
        )
        shipped_at = created_at + timedelta(days=1) if status in shipped_statuses else None
        delivered_at = created_at + timedelta(days=4) if status in delivered_statuses else None
        item_count = 1 + order_index % 2
        total = Decimal("0.00")
        for item_offset in range(item_count):
            sku_number = ((order_index * 7 + item_offset * 13) % 1200) + 1
            sku_id = f"sku_demo_{sku_number:06d}"
            quantity = 1 + ((order_index + item_offset) % 2)
            unit_price = sku_prices[sku_id]
            subtotal = refund_amount_snapshot(unit_price, quantity)
            total += subtotal
            item = {
                "id": f"item_demo_{order_index:06d}_{item_offset + 1}",
                "order_id": order_id,
                "product_id": sku_products[sku_id],
                "sku_id": sku_id,
                "name_snapshot": sku_names[sku_id],
                "quantity": quantity,
                "unit_price_snapshot": unit_price,
                "subtotal_snapshot": subtotal,
                "price_snapshot_id": f"ps_demo_{sku_number:06d}",
                "item_condition": "unopened" if order_index % 5 else "opened_intact",
            }
            order_items.append(item)
            first_item_by_order.setdefault(order_id, item)
        orders.append(
            {
                "id": order_id,
                "user_id": user_id,
                "status": status.value,
                "total_amount": total,
                "currency": "CNY",
                "state_version": 1 + order_index % 4,
                "created_at": created_at,
                "paid_at": paid_at,
                "shipped_at": shipped_at,
                "delivered_at": delivered_at,
                "updated_at": BASE_TIME,
            }
        )
        if status in shipped_statuses and shipped_at is not None:
            event_count = 2 + order_index % 5
            for sequence in range(1, event_count + 1):
                logistics_events.append(
                    {
                        "id": f"log_demo_{order_index:06d}_{sequence}",
                        "order_id": order_id,
                        "sequence": sequence,
                        "carrier": "云途演示物流",
                        "tracking_number_masked": f"YT****{order_index:06d}",
                        "status_code": "delivered"
                        if sequence == event_count and delivered_at
                        else "in_transit",
                        "description": f"演示物流节点 {sequence}/{event_count}",
                        "occurred_at": shipped_at + timedelta(hours=sequence * 8),
                        "as_of": BASE_TIME,
                    }
                )

    request_types = list(AfterSalesRequestType)
    rules: list[dict[str, Any]] = []
    for category_index, category in enumerate(categories):
        for request_index, request_type in enumerate(request_types):
            for tier in range(4):
                rule_number = len(rules) + 1
                rules.append(
                    {
                        "id": f"rule_demo_{rule_number:03d}",
                        "rule_code": f"{category.upper()}_{request_type.value.upper()}_T{tier + 1}",
                        "version": "1.0",
                        "category": category,
                        "request_type": request_type.value,
                        "window_days": 7 + tier * 7 + category_index,
                        "allowed_conditions": ["unopened", "opened_intact", "defective"],
                        "excluded_reason_codes": ["USER_DAMAGE", "MISSING_PARTS"],
                        "required_materials": ["商品照片", "包装照片"]
                        if request_index < 2
                        else ["故障视频"],
                        "active_from": BASE_TIME - timedelta(days=30),
                        "active_until": None,
                        "enabled": tier == 0,
                    }
                )

    ticket_orders = [order for order in orders if order["status"] == OrderStatus.DELIVERED.value][
        :100
    ]
    service_tickets: list[dict[str, Any]] = []
    approval_tasks: list[dict[str, Any]] = []
    for ticket_index, order in enumerate(ticket_orders, start=1):
        order_id = str(order["id"])
        principal_id = str(order["user_id"])
        item = first_item_by_order[order_id]
        idempotency_key = f"idem_demo_ticket_{ticket_index:06d}"
        fingerprint = idempotency_fingerprint(
            principal_id=principal_id,
            order_id=order_id,
            item_id=str(item["id"]),
            request_type=AfterSalesRequestType.RETURN,
            idempotency_key=idempotency_key,
        )
        amount = Decimal(str(item["subtotal_snapshot"]))
        ticket_id = f"ticket_demo_{ticket_index:06d}"
        service_tickets.append(
            {
                "id": ticket_id,
                "principal_id": principal_id,
                "order_id": order_id,
                "item_id": item["id"],
                "request_type": AfterSalesRequestType.RETURN.value,
                "reason_code": "DEMO_RETURN",
                "description_redacted": "演示售后工单，不包含真实个人信息。",
                "idempotency_key": idempotency_key,
                "idempotency_fingerprint": fingerprint,
                "risk_level": "high",
                "approval_status": "pending_human_approval",
                "refund_amount_snapshot": amount,
                "state_version": 1,
                "created_at": BASE_TIME - timedelta(hours=ticket_index),
                "updated_at": BASE_TIME - timedelta(hours=ticket_index),
            }
        )
        approval_tasks.append(
            {
                "id": f"approval_demo_{ticket_index:06d}",
                "ticket_id": ticket_id,
                "action_type": "mock_refund_review",
                "status": "pending_human_approval",
                "amount_snapshot": amount,
                "state_version": 1,
                "created_at": BASE_TIME - timedelta(hours=ticket_index),
                "decided_at": None,
            }
        )

    knowledge_versions = [
        {
            "id": "kv_demo_empty_v1",
            "version": "knowledge-empty-v1",
            "status": "pending_ingestion",
            "manifest_json": {
                "document_count": 0,
                "note": (
                    "P5 will populate knowledge documents; dynamic commerce facts are excluded."
                ),
            },
            "activated_at": None,
            "created_at": BASE_TIME,
        }
    ]
    rows: dict[type[Any], list[dict[str, Any]]] = {
        User: users,
        Product: products,
        Sku: skus,
        Inventory: inventory,
        Order: orders,
        OrderItem: order_items,
        LogisticsEvent: logistics_events,
        AfterSalesRule: rules,
        ServiceTicket: service_tickets,
        ApprovalTask: approval_tasks,
        KnowledgeVersion: knowledge_versions,
    }
    scenario_statuses = [
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.PAID,
        OrderStatus.PACKED,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLATION_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    ]
    selected_scenarios = {status.value: scenario_ids[status.value] for status in scenario_statuses}
    selected_scenarios["catalog_budget_headphones"] = "prd_demo_000001"
    selected_scenarios["inventory_zero_example"] = next(
        str(row["id"]) for row in inventory if row["available_quantity"] == 0
    )
    rng.shuffle(products)
    rng.shuffle(skus)
    rng.shuffle(inventory)
    return DemoDataset(
        rows=rows,
        scenario_ids=selected_scenarios,
        fingerprint=_fingerprint(rows),
    )
