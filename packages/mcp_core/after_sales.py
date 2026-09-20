from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.models import (
    AfterSalesRule,
    ApprovalTask,
    OrderItem,
    Product,
    ServiceTicket,
)
from packages.business.repositories import BusinessRepository
from packages.business.rules import (
    AfterSalesPolicy,
    ItemCondition,
    evaluate_after_sales,
    evaluate_cancellation,
    idempotency_fingerprint,
)
from packages.contracts.after_sales import (
    AfterSalesEligibilityInput,
    AfterSalesEligibilityResult,
    AfterSalesRequestType,
    PrefillServiceTicketInput,
    ServiceTicketDraft,
)
from packages.contracts.common import ErrorCode, RiskLevel, ToolEnvelope
from packages.contracts.order import OrderStatus
from packages.mcp_core.context import TrustedToolContext
from packages.mcp_core.errors import ToolFailure
from packages.mcp_core.runtime import execute_tool

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ORDER_NOT_FOUND_MESSAGE = "订单或订单项不存在，或当前主体无权访问。"


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _redact_description(value: str) -> str:
    return EMAIL.sub("[EMAIL]", PHONE.sub("[PHONE]", value))


class AfterSalesToolService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = _utc_now_naive,
    ) -> None:
        self.factory = factory
        self.clock = clock

    async def _eligibility(
        self,
        session: AsyncSession,
        *,
        principal_id: str,
        payload: AfterSalesEligibilityInput,
    ) -> tuple[AfterSalesEligibilityResult, OrderItem]:
        repository = BusinessRepository(session)
        order = await repository.get_owned_order(principal_id, payload.order_id)
        item = await repository.get_owned_order_item(
            principal_id,
            payload.order_id,
            payload.item_id,
        )
        if order is None or item is None:
            raise ToolFailure(ErrorCode.NOT_FOUND, ORDER_NOT_FOUND_MESSAGE)
        status = OrderStatus(order.status)
        if payload.request_type == AfterSalesRequestType.CANCEL:
            cancellation = evaluate_cancellation(status)
            return (
                AfterSalesEligibilityResult(
                    eligible=cancellation.eligible,
                    rule_code="ORDER_CANCELLATION_V1",
                    rule_version=cancellation.rule_version,
                    required_materials=[],
                    reason_code=cancellation.reason_code,
                    reason=cancellation.reason,
                    risk_level=cancellation.risk_level,
                    requires_human_approval=cancellation.requires_human_approval,
                ),
                item,
            )

        product = await session.scalar(select(Product).where(Product.id == item.product_id))
        if product is None:
            raise ToolFailure(ErrorCode.DEPENDENCY_ERROR, "订单项引用的商品不存在。")
        now = self.clock()
        rule = await session.scalar(
            select(AfterSalesRule)
            .where(
                AfterSalesRule.category == product.category,
                AfterSalesRule.request_type == payload.request_type.value,
                AfterSalesRule.enabled.is_(True),
                AfterSalesRule.active_from <= now,
                or_(AfterSalesRule.active_until.is_(None), AfterSalesRule.active_until >= now),
            )
            .order_by(AfterSalesRule.version.desc(), AfterSalesRule.rule_code)
        )
        if rule is None:
            return (
                AfterSalesEligibilityResult(
                    eligible=False,
                    rule_code="NO_ACTIVE_RULE",
                    rule_version="0",
                    required_materials=[],
                    reason_code="RULE_NOT_FOUND",
                    reason="当前类目和申请类型没有生效的售后规则。",
                    risk_level=RiskLevel.MEDIUM,
                    requires_human_approval=False,
                ),
                item,
            )
        policy = AfterSalesPolicy(
            rule_code=rule.rule_code,
            version=rule.version,
            category=rule.category,
            request_type=AfterSalesRequestType(rule.request_type),
            window_days=rule.window_days,
            allowed_conditions=frozenset(ItemCondition(value) for value in rule.allowed_conditions),
            excluded_reason_codes=frozenset(rule.excluded_reason_codes),
            required_materials=tuple(rule.required_materials),
            active_from=rule.active_from,
            active_until=rule.active_until,
            enabled=rule.enabled,
        )
        decision = evaluate_after_sales(
            order_status=status,
            delivered_at=order.delivered_at,
            category=product.category,
            request_type=payload.request_type,
            item_condition=ItemCondition(item.item_condition),
            reason_code=payload.reason_code,
            policy=policy,
            now=now,
        )
        return (
            AfterSalesEligibilityResult(
                eligible=decision.eligible,
                rule_code=decision.rule_code,
                rule_version=decision.rule_version,
                deadline=decision.deadline,
                required_materials=list(decision.required_materials),
                reason_code=decision.reason_code,
                reason=decision.reason,
                risk_level=RiskLevel.HIGH if decision.eligible else RiskLevel.MEDIUM,
                requires_human_approval=decision.eligible,
            ),
            item,
        )

    async def check_eligibility(
        self,
        context: TrustedToolContext,
        payload: AfterSalesEligibilityInput,
    ) -> ToolEnvelope[AfterSalesEligibilityResult]:
        async def operation() -> AfterSalesEligibilityResult:
            async with self.factory() as session:
                eligibility, _ = await self._eligibility(
                    session,
                    principal_id=context.principal_id,
                    payload=payload,
                )
            return eligibility

        return await execute_tool(
            name="check_after_sales_eligibility",
            context=context,
            required_scope="after-sales:read",
            operation=operation,
        )

    @staticmethod
    def _draft(
        ticket: ServiceTicket, approval: ApprovalTask, *, duplicate: bool
    ) -> ServiceTicketDraft:
        amount = (
            format(Decimal(ticket.refund_amount_snapshot), "f")
            if ticket.refund_amount_snapshot is not None
            else None
        )
        return ServiceTicketDraft(
            ticket_id=ticket.id,
            approval_id=approval.id,
            order_id=ticket.order_id,
            item_id=ticket.item_id,
            risk_level=RiskLevel(ticket.risk_level),
            approval_status=ticket.approval_status,
            approval_state_version=approval.state_version,
            approval_expires_at=approval.expires_at,
            refund_amount_snapshot=amount,
            state_version=ticket.state_version,
            created_at=ticket.created_at,
            duplicate=duplicate,
        )

    async def _existing_ticket(
        self,
        session: AsyncSession,
        *,
        principal_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> ServiceTicket | None:
        existing = await session.scalar(
            select(ServiceTicket).where(
                ServiceTicket.principal_id == principal_id,
                or_(
                    ServiceTicket.idempotency_key == idempotency_key,
                    ServiceTicket.idempotency_fingerprint == fingerprint,
                ),
            )
        )
        if existing is not None and existing.idempotency_fingerprint != fingerprint:
            raise ToolFailure(
                ErrorCode.STATE_CONFLICT,
                "该幂等键已经绑定到另一项售后请求。",
            )
        return existing

    async def prefill_service_ticket(
        self,
        context: TrustedToolContext,
        payload: PrefillServiceTicketInput,
    ) -> ToolEnvelope[ServiceTicketDraft]:
        async def operation() -> ServiceTicketDraft:
            fingerprint = idempotency_fingerprint(
                principal_id=context.principal_id,
                order_id=payload.order_id,
                item_id=payload.item_id,
                request_type=payload.request_type,
                idempotency_key=payload.idempotency_key,
            )
            async with self.factory() as session:
                existing = await self._existing_ticket(
                    session,
                    principal_id=context.principal_id,
                    idempotency_key=payload.idempotency_key,
                    fingerprint=fingerprint,
                )
                if existing is not None:
                    existing_approval = await session.scalar(
                        select(ApprovalTask).where(ApprovalTask.ticket_id == existing.id)
                    )
                    if existing_approval is None:
                        raise ToolFailure(
                            ErrorCode.DEPENDENCY_ERROR,
                            "售后工单缺少对应审批任务。",
                        ) from None
                    return self._draft(existing, existing_approval, duplicate=True)
                eligibility, item = await self._eligibility(
                    session,
                    principal_id=context.principal_id,
                    payload=payload,
                )
                if not eligibility.eligible:
                    raise ToolFailure(ErrorCode.STATE_CONFLICT, eligibility.reason)
                now = self.clock()
                ticket_id = f"ticket_{uuid4().hex}"
                approval_status = (
                    "pending_human_approval"
                    if eligibility.requires_human_approval
                    else "draft_ready"
                )
                ticket = ServiceTicket(
                    id=ticket_id,
                    principal_id=context.principal_id,
                    order_id=payload.order_id,
                    item_id=payload.item_id,
                    request_type=payload.request_type.value,
                    reason_code=payload.reason_code,
                    description_redacted=_redact_description(payload.description),
                    idempotency_key=payload.idempotency_key,
                    idempotency_fingerprint=fingerprint,
                    risk_level=eligibility.risk_level.value,
                    approval_status=approval_status,
                    refund_amount_snapshot=item.subtotal_snapshot,
                    state_version=1,
                    created_at=now,
                    updated_at=now,
                )
                approval = ApprovalTask(
                    id=f"approval_{uuid4().hex}",
                    ticket_id=ticket_id,
                    action_type=(
                        "mock_cancel_draft"
                        if payload.request_type == AfterSalesRequestType.CANCEL
                        else "mock_refund_review"
                    ),
                    status=approval_status,
                    amount_snapshot=item.subtotal_snapshot,
                    state_version=1,
                    session_id=None,
                    checkpoint_thread_id=None,
                    decided_by=None,
                    decision_reason=None,
                    expires_at=now + timedelta(hours=24),
                    resumed_at=None,
                    resume_result=None,
                    created_at=now,
                    decided_at=None,
                    updated_at=now,
                )
                session.add(ticket)
                try:
                    # ORM models intentionally avoid relationship loading. Flush the parent
                    # explicitly so MySQL always sees the ticket before its approval row.
                    await session.flush()
                    session.add(approval)
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    existing = await self._existing_ticket(
                        session,
                        principal_id=context.principal_id,
                        idempotency_key=payload.idempotency_key,
                        fingerprint=fingerprint,
                    )
                    if existing is None:
                        raise
                    existing_approval = await session.scalar(
                        select(ApprovalTask).where(ApprovalTask.ticket_id == existing.id)
                    )
                    if existing_approval is None:
                        raise ToolFailure(
                            ErrorCode.DEPENDENCY_ERROR,
                            "售后工单缺少对应审批任务。",
                        ) from None
                    return self._draft(existing, existing_approval, duplicate=True)
                await session.refresh(ticket)
                await session.refresh(approval)
                return self._draft(ticket, approval, duplicate=False)

        return await execute_tool(
            name="prefill_service_ticket",
            context=context,
            required_scope="after-sales:write",
            operation=operation,
        )
