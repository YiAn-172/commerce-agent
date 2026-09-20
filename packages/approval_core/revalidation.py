from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent_core.contracts import ToolGateway
from packages.approval_core.contracts import ApprovalSnapshot, RevalidationResult
from packages.business.models import ServiceTicket


class McpApprovalRevalidator:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        tools: ToolGateway,
        *,
        deadline_ms: int = 5000,
    ) -> None:
        self.factory = factory
        self.tools = tools
        self.deadline_ms = deadline_ms

    async def revalidate(self, approval: ApprovalSnapshot) -> RevalidationResult:
        async with self.factory() as session:
            ticket = await session.get(ServiceTicket, approval.ticket_id)
            if ticket is None:
                return RevalidationResult(valid=False, reason="售后工单不存在。")
            arguments = {
                "order_id": ticket.order_id,
                "item_id": ticket.item_id,
                "request_type": ticket.request_type,
                "reason_code": ticket.reason_code,
            }
        envelope = await self.tools.call(
            "check_after_sales_eligibility",
            arguments,
            principal_id=approval.principal_id,
            trace_id=f"tr_revalidate_{approval.approval_id}",
            request_id=f"req_revalidate_{approval.approval_id}",
            deadline_ms=self.deadline_ms,
        )
        if not envelope.get("ok"):
            error = envelope.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            return RevalidationResult(
                valid=False,
                reason=str(message or "售后资格复检工具调用失败。"),
            )
        data = envelope.get("data")
        if not isinstance(data, dict):
            return RevalidationResult(valid=False, reason="售后资格复检缺少结构化结果。")
        return RevalidationResult(
            valid=bool(data.get("eligible")),
            reason=str(data.get("reason", "售后资格复检完成。")),
        )
