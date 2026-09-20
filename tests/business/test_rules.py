from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from packages.business.demo_data import build_demo_dataset
from packages.business.rules import (
    AfterSalesPolicy,
    CancellationAction,
    ItemCondition,
    can_transition,
    evaluate_after_sales,
    evaluate_cancellation,
    idempotency_fingerprint,
    price_is_current,
    refund_amount_snapshot,
    require_transition,
)
from packages.contracts.after_sales import AfterSalesRequestType
from packages.contracts.order import OrderStatus

NOW = datetime(2026, 9, 15, 4, 0, 0)


def policy() -> AfterSalesPolicy:
    return AfterSalesPolicy(
        rule_code="HEADPHONES_RETURN_V1",
        version="1.0",
        category="headphones",
        request_type=AfterSalesRequestType.RETURN,
        window_days=7,
        allowed_conditions=frozenset({ItemCondition.UNOPENED, ItemCondition.DEFECTIVE}),
        excluded_reason_codes=frozenset({"USER_DAMAGE"}),
        required_materials=("商品照片",),
        active_from=NOW - timedelta(days=30),
    )


def test_order_state_machine_allows_only_declared_transitions() -> None:
    assert can_transition(OrderStatus.PENDING_PAYMENT, OrderStatus.PAID)
    assert can_transition(OrderStatus.PENDING_PAYMENT, OrderStatus.CANCELLED)
    assert can_transition(OrderStatus.PACKED, OrderStatus.SHIPPED)
    assert can_transition(OrderStatus.DELIVERED, OrderStatus.AFTER_SALES_REQUESTED)
    assert not can_transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED)
    assert not can_transition(OrderStatus.REFUNDED, OrderStatus.PAID)
    with pytest.raises(ValueError, match="invalid order transition"):
        require_transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED)


@pytest.mark.parametrize(
    ("status", "action", "eligible", "approval"),
    [
        (
            OrderStatus.PENDING_PAYMENT,
            CancellationAction.CREATE_DIRECT_DRAFT,
            True,
            False,
        ),
        (
            OrderStatus.PAID,
            CancellationAction.CREATE_APPROVAL_DRAFT,
            True,
            True,
        ),
        (
            OrderStatus.PACKED,
            CancellationAction.CREATE_APPROVAL_DRAFT,
            True,
            True,
        ),
        (
            OrderStatus.SHIPPED,
            CancellationAction.REJECT_DIRECT_CANCELLATION,
            False,
            False,
        ),
    ],
)
def test_cancellation_is_deterministic(
    status: OrderStatus,
    action: CancellationAction,
    eligible: bool,
    approval: bool,
) -> None:
    decision = evaluate_cancellation(status)
    assert decision.action == action
    assert decision.eligible is eligible
    assert decision.requires_human_approval is approval


def test_after_sales_eligibility_uses_state_window_condition_and_exclusions() -> None:
    accepted = evaluate_after_sales(
        order_status=OrderStatus.DELIVERED,
        delivered_at=NOW - timedelta(days=2),
        category="headphones",
        request_type=AfterSalesRequestType.RETURN,
        item_condition=ItemCondition.UNOPENED,
        reason_code="DO_NOT_WANT",
        policy=policy(),
        now=NOW,
    )
    assert accepted.eligible is True
    assert accepted.reason_code == "ELIGIBLE"

    expired = evaluate_after_sales(
        order_status=OrderStatus.DELIVERED,
        delivered_at=NOW - timedelta(days=8),
        category="headphones",
        request_type=AfterSalesRequestType.RETURN,
        item_condition=ItemCondition.UNOPENED,
        reason_code="DO_NOT_WANT",
        policy=policy(),
        now=NOW,
    )
    assert expired.eligible is False
    assert expired.reason_code == "WINDOW_EXPIRED"

    excluded = evaluate_after_sales(
        order_status=OrderStatus.DELIVERED,
        delivered_at=NOW - timedelta(days=1),
        category="headphones",
        request_type=AfterSalesRequestType.RETURN,
        item_condition=ItemCondition.DEFECTIVE,
        reason_code="USER_DAMAGE",
        policy=policy(),
        now=NOW,
    )
    assert excluded.eligible is False
    assert excluded.reason_code == "REASON_EXCLUDED"


def test_refund_uses_immutable_order_item_snapshot() -> None:
    assert refund_amount_snapshot(Decimal("129.995"), 2) == Decimal("259.99")
    with pytest.raises(ValueError):
        refund_amount_snapshot(Decimal("10.00"), 0)


def test_idempotency_fingerprint_is_stable_and_subject_bound() -> None:
    def fingerprint(principal_id: str) -> str:
        return idempotency_fingerprint(
            principal_id=principal_id,
            order_id="ord_demo_000001",
            item_id="item_demo_000001_1",
            request_type=AfterSalesRequestType.RETURN,
            idempotency_key="idem_demo_00000001",
        )

    first = fingerprint("usr_demo_0001")
    assert first == fingerprint("usr_demo_0001")
    assert first != fingerprint("usr_demo_0002")


def test_price_snapshot_expiry_is_code_driven() -> None:
    assert price_is_current(NOW + timedelta(seconds=1), now=NOW)
    assert not price_is_current(NOW - timedelta(seconds=1), now=NOW)


def test_demo_dataset_is_reproducible_and_has_ten_stable_scenarios() -> None:
    first = build_demo_dataset(20260915)
    second = build_demo_dataset(20260915)
    assert first.fingerprint == second.fingerprint
    assert len(first.scenario_ids) == 10
    assert len(first.rows) == 11
