from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Select, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.approval_core.contracts import (
    ApprovalDecision,
    ApprovalSnapshot,
    ApprovalWorkflowResult,
)
from packages.business.models import ApprovalTask, ServiceTicket

PENDING = "pending_human_approval"
DECIDED_STATUSES = {item.value for item in ApprovalDecision}


class ApprovalNotFound(RuntimeError):
    pass


class ApprovalStateConflict(RuntimeError):
    pass


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApprovalService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    @staticmethod
    def _snapshot(
        task: ApprovalTask, ticket: ServiceTicket, *, duplicate: bool = False
    ) -> ApprovalSnapshot:
        amount = (
            format(Decimal(task.amount_snapshot), "f")
            if task.amount_snapshot is not None
            else None
        )
        return ApprovalSnapshot(
            approval_id=task.id,
            ticket_id=task.ticket_id,
            principal_id=ticket.principal_id,
            action_type=task.action_type,
            status=task.status,
            amount_snapshot=amount,
            state_version=task.state_version,
            session_id=task.session_id,
            checkpoint_thread_id=task.checkpoint_thread_id,
            decided_by=task.decided_by,
            decision_reason=task.decision_reason,
            expires_at=task.expires_at,
            resumed_at=task.resumed_at,
            duplicate=duplicate,
        )

    async def _load(
        self, session: AsyncSession, approval_id: str
    ) -> tuple[ApprovalTask, ServiceTicket]:
        task = await session.get(ApprovalTask, approval_id)
        if task is None:
            raise ApprovalNotFound(f"approval not found: {approval_id}")
        ticket = await session.get(ServiceTicket, task.ticket_id)
        if ticket is None:
            raise ApprovalStateConflict("approval references a missing service ticket")
        return task, ticket

    async def get(self, approval_id: str) -> ApprovalSnapshot:
        async with self.factory() as session:
            task, ticket = await self._load(session, approval_id)
            return self._snapshot(task, ticket)

    async def list_pending(self, *, limit: int = 100) -> list[ApprovalSnapshot]:
        statement: Select[tuple[ApprovalTask]] = (
            select(ApprovalTask)
            .where(ApprovalTask.status == PENDING)
            .order_by(ApprovalTask.created_at)
            .limit(limit)
        )
        async with self.factory() as session:
            tasks = list((await session.scalars(statement)).all())
            snapshots: list[ApprovalSnapshot] = []
            for task in tasks:
                ticket = await session.get(ServiceTicket, task.ticket_id)
                if ticket is not None:
                    snapshots.append(self._snapshot(task, ticket))
            return snapshots

    async def bind_workflow(
        self,
        approval_id: str,
        *,
        session_id: str,
        checkpoint_thread_id: str,
        expected_state_version: int,
    ) -> ApprovalSnapshot:
        now = utc_now_naive()
        async with self.factory() as session:
            task, ticket = await self._load(session, approval_id)
            if (
                task.session_id == session_id
                and task.checkpoint_thread_id == checkpoint_thread_id
            ):
                return self._snapshot(task, ticket, duplicate=True)
            result = await session.execute(
                update(ApprovalTask)
                .where(
                    ApprovalTask.id == approval_id,
                    ApprovalTask.status == PENDING,
                    ApprovalTask.state_version == expected_state_version,
                    ApprovalTask.session_id.is_(None),
                    ApprovalTask.checkpoint_thread_id.is_(None),
                )
                .values(
                    session_id=session_id,
                    checkpoint_thread_id=checkpoint_thread_id,
                    state_version=expected_state_version + 1,
                    updated_at=now,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ApprovalStateConflict("approval workflow bind conflict")
            await session.commit()
            task, ticket = await self._load(session, approval_id)
            return self._snapshot(task, ticket)

    async def decide(
        self,
        approval_id: str,
        *,
        decision: ApprovalDecision,
        reason: str,
        decided_by: str,
        expected_state_version: int,
        now: datetime | None = None,
    ) -> ApprovalSnapshot:
        if decision == ApprovalDecision.EXPIRED:
            raise ValueError("expired is assigned by the expiry worker, not a human decision")
        decided_at = now or utc_now_naive()
        async with self.factory() as session:
            task, ticket = await self._load(session, approval_id)
            if task.status == decision.value:
                if task.decided_by == decided_by and task.decision_reason == reason:
                    return self._snapshot(task, ticket, duplicate=True)
                raise ApprovalStateConflict("approval already has a different decision")
            if task.status != PENDING:
                raise ApprovalStateConflict("approval already has a different decision")
            if task.expires_at is not None and task.expires_at <= decided_at:
                raise ApprovalStateConflict("approval has expired and cannot be decided")
            result = await session.execute(
                update(ApprovalTask)
                .where(
                    ApprovalTask.id == approval_id,
                    ApprovalTask.status == PENDING,
                    ApprovalTask.state_version == expected_state_version,
                    or_(ApprovalTask.expires_at.is_(None), ApprovalTask.expires_at > decided_at),
                )
                .values(
                    status=decision.value,
                    state_version=expected_state_version + 1,
                    decided_by=decided_by,
                    decision_reason=reason,
                    decided_at=decided_at,
                    updated_at=decided_at,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ApprovalStateConflict("approval state_version conflict")
            await session.execute(
                update(ServiceTicket)
                .where(ServiceTicket.id == ticket.id)
                .values(
                    approval_status=decision.value,
                    state_version=ServiceTicket.state_version + 1,
                    updated_at=decided_at,
                )
            )
            await session.commit()
            task, ticket = await self._load(session, approval_id)
            return self._snapshot(task, ticket)

    async def expire_due(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[ApprovalSnapshot]:
        expired_at = now or utc_now_naive()
        statement: Select[tuple[ApprovalTask]] = (
            select(ApprovalTask)
            .where(
                ApprovalTask.status == PENDING,
                ApprovalTask.expires_at.is_not(None),
                ApprovalTask.expires_at <= expired_at,
            )
            .order_by(ApprovalTask.expires_at)
            .limit(limit)
        )
        expired: list[ApprovalSnapshot] = []
        async with self.factory() as session:
            task_ids = list((await session.scalars(statement)).all())
            for selected in task_ids:
                result = await session.execute(
                    update(ApprovalTask)
                    .where(
                        ApprovalTask.id == selected.id,
                        ApprovalTask.status == PENDING,
                        ApprovalTask.state_version == selected.state_version,
                    )
                    .values(
                        status=ApprovalDecision.EXPIRED.value,
                        state_version=selected.state_version + 1,
                        decided_by="system_expiry",
                        decision_reason="审批超过有效期，已自动关闭。",
                        decided_at=expired_at,
                        updated_at=expired_at,
                    )
                )
                if cast(CursorResult[Any], result).rowcount != 1:
                    continue
                await session.execute(
                    update(ServiceTicket)
                    .where(ServiceTicket.id == selected.ticket_id)
                    .values(
                        approval_status=ApprovalDecision.EXPIRED.value,
                        state_version=ServiceTicket.state_version + 1,
                        updated_at=expired_at,
                    )
                )
                await session.commit()
                task, ticket = await self._load(session, selected.id)
                expired.append(self._snapshot(task, ticket))
            return expired

    async def cancel_stale_approval(
        self, approval_id: str, *, expected_state_version: int, reason: str
    ) -> ApprovalSnapshot:
        now = utc_now_naive()
        async with self.factory() as session:
            task, ticket = await self._load(session, approval_id)
            result = await session.execute(
                update(ApprovalTask)
                .where(
                    ApprovalTask.id == approval_id,
                    ApprovalTask.status == ApprovalDecision.APPROVED.value,
                    ApprovalTask.state_version == expected_state_version,
                )
                .values(
                    status=ApprovalDecision.CANCELLED.value,
                    state_version=expected_state_version + 1,
                    decision_reason=reason,
                    updated_at=now,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ApprovalStateConflict("stale approval compensation conflict")
            await session.execute(
                update(ServiceTicket)
                .where(ServiceTicket.id == ticket.id)
                .values(
                    approval_status=ApprovalDecision.CANCELLED.value,
                    state_version=ServiceTicket.state_version + 1,
                    updated_at=now,
                )
            )
            await session.commit()
            task, ticket = await self._load(session, approval_id)
            return self._snapshot(task, ticket)

    async def mark_resumed(
        self,
        approval_id: str,
        *,
        expected_state_version: int,
        result_payload: dict[str, Any],
    ) -> ApprovalSnapshot:
        now = utc_now_naive()
        async with self.factory() as session:
            task, ticket = await self._load(session, approval_id)
            if task.resumed_at is not None:
                return self._snapshot(task, ticket, duplicate=True)
            result = await session.execute(
                update(ApprovalTask)
                .where(
                    ApprovalTask.id == approval_id,
                    ApprovalTask.status.in_(DECIDED_STATUSES),
                    ApprovalTask.state_version == expected_state_version,
                    ApprovalTask.resumed_at.is_(None),
                )
                .values(
                    resumed_at=now,
                    resume_result=result_payload,
                    state_version=expected_state_version + 1,
                    updated_at=now,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ApprovalStateConflict("approval resume conflict")
            await session.commit()
            task, ticket = await self._load(session, approval_id)
            return self._snapshot(task, ticket)

    async def get_resume_result(self, approval_id: str) -> ApprovalWorkflowResult | None:
        async with self.factory() as session:
            task, _ = await self._load(session, approval_id)
            if task.resume_result is None:
                return None
            payload = dict(task.resume_result)
            payload["duplicate"] = True
            return ApprovalWorkflowResult.model_validate(payload, strict=False)

    async def list_recoverable(self, *, limit: int = 100) -> list[ApprovalSnapshot]:
        statement: Select[tuple[ApprovalTask]] = (
            select(ApprovalTask)
            .where(
                ApprovalTask.status.in_(DECIDED_STATUSES),
                ApprovalTask.resumed_at.is_(None),
                ApprovalTask.checkpoint_thread_id.is_not(None),
            )
            .order_by(ApprovalTask.decided_at)
            .limit(limit)
        )
        async with self.factory() as session:
            tasks = list((await session.scalars(statement)).all())
            snapshots: list[ApprovalSnapshot] = []
            for task in tasks:
                ticket = await session.get(ServiceTicket, task.ticket_id)
                if ticket is not None:
                    snapshots.append(self._snapshot(task, ticket))
            return snapshots
