from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.approval_core.contracts import (
    ApprovalDecision,
    ApprovalResume,
    RevalidationResult,
)
from packages.approval_core.service import ApprovalService
from packages.approval_core.workflow import (
    ApprovalRunner,
    ApprovalWorkflowContext,
    build_approval_graph,
)
from tests.approval_core.conftest import NOW


class FakeRevalidator:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls = 0

    async def revalidate(self, approval: object) -> RevalidationResult:
        self.calls += 1
        return RevalidationResult(
            valid=self.valid,
            reason="售后资格仍然有效。" if self.valid else "订单状态已经发生变化。",
        )


def runner(
    factory: async_sessionmaker[AsyncSession], *, valid: bool = True
) -> tuple[ApprovalRunner, ApprovalService, FakeRevalidator]:
    service = ApprovalService(factory)
    revalidator = FakeRevalidator(valid)
    context = ApprovalWorkflowContext(service=service, revalidator=revalidator)
    return ApprovalRunner(build_approval_graph(InMemorySaver()), context), service, revalidator


@pytest.mark.asyncio
async def test_interrupt_approve_resume_and_duplicate_resume(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    workflow, service, revalidator = runner(approval_factory)
    waiting = await workflow.start(
        "approval_test_000001",
        session_id="ses_approval_000001",
        expected_state_version=1,
    )
    assert waiting.checkpoint_thread_id == "approval_approval_test_000001"
    assert waiting.state_version == 2

    decided = await service.decide(
        waiting.approval_id,
        decision=ApprovalDecision.APPROVED,
        reason="客服核验材料后通过。",
        decided_by="agent_001",
        expected_state_version=waiting.state_version,
        now=NOW,
    )
    command = ApprovalResume(
        decision=ApprovalDecision.APPROVED,
        state_version=decided.state_version,
        decided_by="agent_001",
    )
    result = await workflow.resume(waiting.approval_id, command)
    duplicate = await workflow.resume(waiting.approval_id, command)
    assert result.status == ApprovalDecision.APPROVED
    assert result.outcome == "mock_action_authorized"
    assert result.duplicate is False
    assert duplicate.duplicate is True
    assert revalidator.calls == 1


@pytest.mark.asyncio
async def test_revalidation_failure_compensates_approved_to_cancelled(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    workflow, service, _ = runner(approval_factory, valid=False)
    waiting = await workflow.start(
        "approval_test_000001",
        session_id="ses_approval_000002",
        expected_state_version=1,
    )
    decided = await service.decide(
        waiting.approval_id,
        decision=ApprovalDecision.APPROVED,
        reason="初审通过。",
        decided_by="agent_001",
        expected_state_version=waiting.state_version,
        now=NOW,
    )
    result = await workflow.resume(
        waiting.approval_id,
        ApprovalResume(
            decision=ApprovalDecision.APPROVED,
            state_version=decided.state_version,
            decided_by="agent_001",
        ),
    )
    assert result.status == ApprovalDecision.CANCELLED
    assert result.outcome == "no_action"
    assert result.revalidation_reason == "订单状态已经发生变化。"
    assert (await service.get(waiting.approval_id)).status == ApprovalDecision.CANCELLED.value


@pytest.mark.asyncio
async def test_recovery_resumes_committed_decision_exactly_once(
    approval_factory: async_sessionmaker[AsyncSession],
) -> None:
    workflow, service, revalidator = runner(approval_factory)
    waiting = await workflow.start(
        "approval_test_000001",
        session_id="ses_approval_recovery",
        expected_state_version=1,
    )
    await service.decide(
        waiting.approval_id,
        decision=ApprovalDecision.REJECTED,
        reason="材料缺失。",
        decided_by="agent_001",
        expected_state_version=waiting.state_version,
        now=NOW,
    )
    first = await workflow.recover()
    second = await workflow.recover()
    assert len(first) == 1
    assert first[0].status == ApprovalDecision.REJECTED
    assert second == []
    assert revalidator.calls == 0
