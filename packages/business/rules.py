from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from packages.contracts.after_sales import AfterSalesRequestType
from packages.contracts.common import RiskLevel
from packages.contracts.order import OrderStatus

MONEY_QUANTUM = Decimal("0.01")
RULE_VERSION = "business-rules-v1"


class ItemCondition(StrEnum):
    UNOPENED = "unopened"
    OPENED_INTACT = "opened_intact"
    DEFECTIVE = "defective"
    DAMAGED_BY_USER = "damaged_by_user"
    MISSING_PARTS = "missing_parts"


class CancellationAction(StrEnum):
    CREATE_DIRECT_DRAFT = "create_direct_draft"
    CREATE_APPROVAL_DRAFT = "create_approval_draft"
    REJECT_DIRECT_CANCELLATION = "reject_direct_cancellation"


@dataclass(frozen=True)
class CancellationDecision:
    action: CancellationAction
    eligible: bool
    requires_human_approval: bool
    risk_level: RiskLevel
    reason_code: str
    reason: str
    rule_version: str = RULE_VERSION


@dataclass(frozen=True)
class AfterSalesPolicy:
    rule_code: str
    version: str
    category: str
    request_type: AfterSalesRequestType
    window_days: int
    allowed_conditions: frozenset[ItemCondition]
    excluded_reason_codes: frozenset[str]
    required_materials: tuple[str, ...]
    active_from: datetime
    active_until: datetime | None = None
    enabled: bool = True


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    rule_code: str
    rule_version: str
    reason_code: str
    reason: str
    deadline: datetime | None
    required_materials: tuple[str, ...]


ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.PACKED, OrderStatus.CANCELLATION_REQUESTED}),
    OrderStatus.PACKED: frozenset({OrderStatus.SHIPPED, OrderStatus.CANCELLATION_REQUESTED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.CANCELLATION_REQUESTED: frozenset(
        {OrderStatus.CANCELLED, OrderStatus.PAID, OrderStatus.PACKED}
    ),
    OrderStatus.DELIVERED: frozenset({OrderStatus.AFTER_SALES_REQUESTED}),
    OrderStatus.AFTER_SALES_REQUESTED: frozenset({OrderStatus.AFTER_SALES_PROCESSING}),
    OrderStatus.AFTER_SALES_PROCESSING: frozenset({OrderStatus.REFUNDED, OrderStatus.CLOSED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
    OrderStatus.CLOSED: frozenset(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in ORDER_TRANSITIONS[current]


def require_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"invalid order transition: {current.value} -> {target.value}")


def evaluate_cancellation(status: OrderStatus) -> CancellationDecision:
    if status == OrderStatus.PENDING_PAYMENT:
        return CancellationDecision(
            action=CancellationAction.CREATE_DIRECT_DRAFT,
            eligible=True,
            requires_human_approval=False,
            risk_level=RiskLevel.LOW,
            reason_code="UNPAID_ORDER_CAN_CANCEL",
            reason="未支付订单可创建取消草稿；演示系统不会调用真实支付渠道。",
        )
    if status in {OrderStatus.PAID, OrderStatus.PACKED}:
        return CancellationDecision(
            action=CancellationAction.CREATE_APPROVAL_DRAFT,
            eligible=True,
            requires_human_approval=True,
            risk_level=RiskLevel.HIGH,
            reason_code="PAID_ORDER_REQUIRES_APPROVAL",
            reason="已支付或已打包订单只能创建人工审批草稿。",
        )
    return CancellationDecision(
        action=CancellationAction.REJECT_DIRECT_CANCELLATION,
        eligible=False,
        requires_human_approval=False,
        risk_level=RiskLevel.MEDIUM,
        reason_code="ORDER_STATE_NOT_CANCELLABLE",
        reason="当前订单状态不能直接取消；已发货订单应进入拒收或退货咨询。",
    )


def evaluate_after_sales(
    *,
    order_status: OrderStatus,
    delivered_at: datetime | None,
    category: str,
    request_type: AfterSalesRequestType,
    item_condition: ItemCondition,
    reason_code: str,
    policy: AfterSalesPolicy,
    now: datetime,
) -> EligibilityDecision:
    deadline = (
        delivered_at + timedelta(days=policy.window_days) if delivered_at is not None else None
    )

    def result(*, eligible: bool, reason_code: str, reason: str) -> EligibilityDecision:
        return EligibilityDecision(
            eligible=eligible,
            rule_code=policy.rule_code,
            rule_version=policy.version,
            reason_code=reason_code,
            reason=reason,
            deadline=deadline,
            required_materials=policy.required_materials,
        )

    if (
        not policy.enabled
        or now < policy.active_from
        or (policy.active_until is not None and now > policy.active_until)
    ):
        return result(
            eligible=False,
            reason_code="RULE_NOT_ACTIVE",
            reason="当前规则未生效。",
        )
    if policy.category != category or policy.request_type != request_type:
        return result(
            eligible=False,
            reason_code="RULE_NOT_APPLICABLE",
            reason="规则不适用于该类目或售后类型。",
        )
    if order_status != OrderStatus.DELIVERED or delivered_at is None:
        return result(
            eligible=False,
            reason_code="ORDER_NOT_DELIVERED",
            reason="订单尚未签收，不能按签收后规则发起该售后类型。",
        )
    if deadline is not None and now > deadline:
        return result(
            eligible=False,
            reason_code="WINDOW_EXPIRED",
            reason="已超过当前规则规定的售后期限。",
        )
    if reason_code in policy.excluded_reason_codes:
        return result(
            eligible=False,
            reason_code="REASON_EXCLUDED",
            reason="该原因属于规则明确排除项。",
        )
    if item_condition not in policy.allowed_conditions:
        return result(
            eligible=False,
            reason_code="ITEM_CONDITION_NOT_ALLOWED",
            reason="当前商品状态不符合该售后规则。",
        )
    return result(
        eligible=True,
        reason_code="ELIGIBLE",
        reason="符合当前版本的售后规则，可创建待确认草稿。",
    )


def refund_amount_snapshot(unit_price: Decimal, quantity: int) -> Decimal:
    if unit_price < 0:
        raise ValueError("unit price cannot be negative")
    if quantity < 1:
        raise ValueError("quantity must be at least one")
    return (unit_price * quantity).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def idempotency_fingerprint(
    *,
    principal_id: str,
    order_id: str,
    item_id: str,
    request_type: AfterSalesRequestType,
    idempotency_key: str,
) -> str:
    canonical = "\x1f".join([principal_id, order_id, item_id, request_type.value, idempotency_key])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def price_is_current(valid_until: datetime, *, now: datetime) -> bool:
    return now <= valid_until
