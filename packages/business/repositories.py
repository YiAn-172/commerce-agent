from __future__ import annotations

from typing import cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business.models import Order, OrderItem, ServiceTicket


class BusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_owned_order(self, principal_id: str, order_id: str) -> Order | None:
        statement: Select[tuple[Order]] = select(Order).where(
            Order.id == order_id,
            Order.user_id == principal_id,
        )
        return cast(Order | None, await self.session.scalar(statement))

    async def get_owned_order_item(
        self,
        principal_id: str,
        order_id: str,
        item_id: str,
    ) -> OrderItem | None:
        statement: Select[tuple[OrderItem]] = (
            select(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.id == order_id,
                Order.user_id == principal_id,
                OrderItem.id == item_id,
            )
        )
        return cast(OrderItem | None, await self.session.scalar(statement))

    async def get_ticket_by_idempotency_key(
        self, principal_id: str, idempotency_key: str
    ) -> ServiceTicket | None:
        statement: Select[tuple[ServiceTicket]] = select(ServiceTicket).where(
            ServiceTicket.principal_id == principal_id,
            ServiceTicket.idempotency_key == idempotency_key,
        )
        return cast(ServiceTicket | None, await self.session.scalar(statement))
