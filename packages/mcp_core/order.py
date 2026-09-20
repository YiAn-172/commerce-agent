from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.models import LogisticsEvent, Order, OrderItem
from packages.business.repositories import BusinessRepository
from packages.contracts.common import ErrorCode, ToolEnvelope
from packages.contracts.order import (
    GetOrderDetailInput,
    ListRecentOrdersInput,
    LogisticsEventView,
    LogisticsTimeline,
    OrderDetail,
    OrderStatus,
    OrderSummary,
    RecentOrderPage,
    TrackLogisticsInput,
)
from packages.contracts.order import OrderItem as OrderItemContract
from packages.mcp_core.context import TrustedToolContext
from packages.mcp_core.errors import ToolFailure
from packages.mcp_core.runtime import execute_tool

ORDER_NOT_FOUND_MESSAGE = "订单不存在或当前主体无权访问。"


def _encode_cursor(created_at: datetime, order_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), order_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        created_at = datetime.fromisoformat(str(value[0]))
        order_id = str(value[1])
        if not order_id.startswith("ord_"):
            raise ValueError
        return created_at, order_id
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ToolFailure(ErrorCode.INVALID_ARGUMENT, "分页游标无效。") from error


class OrderToolService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def get_order_detail(
        self,
        context: TrustedToolContext,
        payload: GetOrderDetailInput,
    ) -> ToolEnvelope[OrderDetail]:
        async def operation() -> OrderDetail:
            async with self.factory() as session:
                repository = BusinessRepository(session)
                order = await repository.get_owned_order(context.principal_id, payload.order_id)
                if order is None:
                    raise ToolFailure(ErrorCode.NOT_FOUND, ORDER_NOT_FOUND_MESSAGE)
                items = (
                    await session.scalars(
                        select(OrderItem)
                        .where(OrderItem.order_id == order.id)
                        .order_by(OrderItem.id)
                    )
                ).all()
            return OrderDetail(
                order_id=order.id,
                status=OrderStatus(order.status),
                items=[
                    OrderItemContract(
                        item_id=item.id,
                        product_id=item.product_id,
                        sku_id=item.sku_id,
                        name=item.name_snapshot,
                        quantity=item.quantity,
                        unit_price=item.unit_price_snapshot,
                    )
                    for item in items
                ],
                total_amount=order.total_amount,
                currency=order.currency,
                state_version=order.state_version,
                created_at=order.created_at,
                updated_at=order.updated_at,
            )

        return await execute_tool(
            name="get_order_detail",
            context=context,
            required_scope="order:read",
            operation=operation,
        )

    async def list_recent_orders(
        self,
        context: TrustedToolContext,
        payload: ListRecentOrdersInput,
    ) -> ToolEnvelope[RecentOrderPage]:
        async def operation() -> RecentOrderPage:
            statement: Select[tuple[Order]] = select(Order).where(
                Order.user_id == context.principal_id
            )
            if payload.cursor:
                created_at, order_id = _decode_cursor(payload.cursor)
                statement = statement.where(
                    or_(
                        Order.created_at < created_at,
                        and_(Order.created_at == created_at, Order.id < order_id),
                    )
                )
            statement = statement.order_by(Order.created_at.desc(), Order.id.desc()).limit(
                payload.limit + 1
            )
            async with self.factory() as session:
                orders = list((await session.scalars(statement)).all())
                visible = orders[: payload.limit]
                counts: dict[str, int] = {}
                if visible:
                    rows = (
                        await session.execute(
                            select(OrderItem.order_id, func.count())
                            .where(OrderItem.order_id.in_([order.id for order in visible]))
                            .group_by(OrderItem.order_id)
                        )
                    ).all()
                    counts = {str(order_id): int(item_count) for order_id, item_count in rows}
            next_cursor = None
            if len(orders) > payload.limit and visible:
                last = visible[-1]
                next_cursor = _encode_cursor(last.created_at, last.id)
            return RecentOrderPage(
                orders=[
                    OrderSummary(
                        order_id=order.id,
                        status=OrderStatus(order.status),
                        item_count=counts[order.id],
                        total_amount=order.total_amount,
                        currency=order.currency,
                        state_version=order.state_version,
                        created_at=order.created_at,
                        updated_at=order.updated_at,
                    )
                    for order in visible
                ],
                next_cursor=next_cursor,
            )

        return await execute_tool(
            name="list_recent_orders",
            context=context,
            required_scope="order:read",
            operation=operation,
        )

    async def track_logistics(
        self,
        context: TrustedToolContext,
        payload: TrackLogisticsInput,
    ) -> ToolEnvelope[LogisticsTimeline]:
        async def operation() -> LogisticsTimeline:
            async with self.factory() as session:
                repository = BusinessRepository(session)
                order = await repository.get_owned_order(context.principal_id, payload.order_id)
                if order is None:
                    raise ToolFailure(ErrorCode.NOT_FOUND, ORDER_NOT_FOUND_MESSAGE)
                events = (
                    await session.scalars(
                        select(LogisticsEvent)
                        .where(LogisticsEvent.order_id == order.id)
                        .order_by(LogisticsEvent.sequence)
                    )
                ).all()
            if not events:
                raise ToolFailure(ErrorCode.NOT_FOUND, "该订单暂时没有可展示的物流轨迹。")
            return LogisticsTimeline(
                order_id=order.id,
                order_status=OrderStatus(order.status),
                events=[
                    LogisticsEventView(
                        sequence=event.sequence,
                        carrier=event.carrier,
                        tracking_number_masked=event.tracking_number_masked,
                        status_code=event.status_code,
                        description=event.description,
                        occurred_at=event.occurred_at,
                        as_of=event.as_of,
                    )
                    for event in events
                ],
                as_of=max(event.as_of for event in events),
            )

        return await execute_tool(
            name="track_logistics",
            context=context,
            required_scope="order:read",
            operation=operation,
        )
