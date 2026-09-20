from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict, cast

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from packages.approval_core.contracts import (
    ApprovalDecision,
    ApprovalResume,
    ApprovalSnapshot,
    ApprovalWorkflowResult,
    RevalidationResult,
)
from packages.approval_core.service import ApprovalService, ApprovalStateConflict


class ApprovalRevalidator(Protocol):
    async def revalidate(self, approval: ApprovalSnapshot) -> RevalidationResult: ...


class ApprovalWorkflowState(TypedDict, total=False):
    approval_id: str
    ticket_id: str
    principal_id: str
    action_type: str
    amount_snapshot: str | None
    state_version: int
    decision: str
    decided_by: str
    revalidation_valid: bool
    revalidation_reason: str
    result: dict[str, Any]


@dataclass(frozen=True)
class ApprovalWorkflowContext:
    service: ApprovalService
    revalidator: ApprovalRevalidator


async def await_human_decision(
    state: ApprovalWorkflowState,
) -> dict[str, Any]:
    value = interrupt(
        {
            "approval_id": state["approval_id"],
            "ticket_id": state["ticket_id"],
            "action_type": state["action_type"],
            "amount_snapshot": state.get("amount_snapshot"),
            "state_version": state["state_version"],
            "allowed_decisions": [
                ApprovalDecision.APPROVED.value,
                ApprovalDecision.REJECTED.value,
                ApprovalDecision.NEEDS_MORE_INFO.value,
                ApprovalDecision.CANCELLED.value,
            ],
        }
    )
    resume = ApprovalResume.model_validate(value, strict=False)
    return {
        "decision": resume.decision.value,
        "decided_by": resume.decided_by,
        "state_version": resume.state_version,
    }


async def revalidate_approval(
    state: ApprovalWorkflowState,
    runtime: Any,
) -> dict[str, Any]:
    context = cast(ApprovalWorkflowContext, runtime.context)
    snapshot = await context.service.get(state["approval_id"])
    if snapshot.status != state["decision"] or snapshot.state_version != state["state_version"]:
        raise ApprovalStateConflict("resume payload does not match the committed decision")
    if snapshot.status != ApprovalDecision.APPROVED.value:
        return {
            "revalidation_valid": True,
            "revalidation_reason": "非批准决策无需重新执行售后资格校验。",
        }
    validation = await context.revalidator.revalidate(snapshot)
    if validation.valid:
        return {
            "revalidation_valid": True,
            "revalidation_reason": validation.reason,
        }
    cancelled = await context.service.cancel_stale_approval(
        snapshot.approval_id,
        expected_state_version=snapshot.state_version,
        reason=f"恢复前资格复检失败：{validation.reason}",
    )
    return {
        "decision": ApprovalDecision.CANCELLED.value,
        "state_version": cancelled.state_version,
        "revalidation_valid": False,
        "revalidation_reason": validation.reason,
    }


async def finalize_approval(
    state: ApprovalWorkflowState,
    runtime: Any,
) -> dict[str, Any]:
    context = cast(ApprovalWorkflowContext, runtime.context)
    decision = ApprovalDecision(state["decision"])
    outcome = {
        ApprovalDecision.APPROVED: "mock_action_authorized",
        ApprovalDecision.REJECTED: "no_action",
        ApprovalDecision.NEEDS_MORE_INFO: "request_more_info",
        ApprovalDecision.EXPIRED: "no_action",
        ApprovalDecision.CANCELLED: "no_action",
    }[decision]
    now = datetime.now(UTC).replace(tzinfo=None)
    result = ApprovalWorkflowResult(
        approval_id=state["approval_id"],
        ticket_id=state["ticket_id"],
        status=decision,
        outcome=outcome,
        revalidation_reason=state.get("revalidation_reason"),
        resumed_at=now,
    )
    await context.service.mark_resumed(
        state["approval_id"],
        expected_state_version=state["state_version"],
        result_payload=result.model_dump(mode="json"),
    )
    return {"result": result.model_dump(mode="json")}


def build_approval_graph(checkpointer: Any) -> Any:
    graph = StateGraph(ApprovalWorkflowState, context_schema=ApprovalWorkflowContext)
    graph.add_node("await_human_decision", await_human_decision)
    graph.add_node("revalidate", revalidate_approval)
    graph.add_node("finalize", finalize_approval)
    graph.add_edge(START, "await_human_decision")
    graph.add_edge("await_human_decision", "revalidate")
    graph.add_edge("revalidate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, name="approval_workflow_v1")


@dataclass
class ApprovalRunner:
    graph: Any
    context: ApprovalWorkflowContext

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def start(
        self,
        approval_id: str,
        *,
        session_id: str,
        expected_state_version: int,
    ) -> ApprovalSnapshot:
        thread_id = f"approval_{approval_id}"
        snapshot = await self.context.service.bind_workflow(
            approval_id,
            session_id=session_id,
            checkpoint_thread_id=thread_id,
            expected_state_version=expected_state_version,
        )
        state = ApprovalWorkflowState(
            approval_id=snapshot.approval_id,
            ticket_id=snapshot.ticket_id,
            principal_id=snapshot.principal_id,
            action_type=snapshot.action_type,
            amount_snapshot=snapshot.amount_snapshot,
            state_version=snapshot.state_version,
        )
        await self.graph.ainvoke(
            state,
            config=self._config(thread_id),
            context=self.context,
        )
        checkpoint = await self.graph.aget_state(self._config(thread_id))
        if not checkpoint.interrupts:
            raise RuntimeError("approval workflow did not reach its human interrupt")
        return snapshot

    async def resume(
        self, approval_id: str, resume: ApprovalResume
    ) -> ApprovalWorkflowResult:
        existing = await self.context.service.get_resume_result(approval_id)
        if existing is not None:
            return existing
        snapshot = await self.context.service.get(approval_id)
        if snapshot.checkpoint_thread_id is None:
            raise ApprovalStateConflict("approval is not bound to a checkpoint")
        result = await self.graph.ainvoke(
            Command(resume=resume.model_dump(mode="json")),
            config=self._config(snapshot.checkpoint_thread_id),
            context=self.context,
        )
        payload = cast(dict[str, Any], result).get("result")
        if not isinstance(payload, dict):
            raise RuntimeError("approval workflow resumed without a terminal result")
        return ApprovalWorkflowResult.model_validate(payload, strict=False)

    async def recover(self, *, limit: int = 100) -> list[ApprovalWorkflowResult]:
        recovered: list[ApprovalWorkflowResult] = []
        for snapshot in await self.context.service.list_recoverable(limit=limit):
            if snapshot.decided_by is None:
                continue
            recovered.append(
                await self.resume(
                    snapshot.approval_id,
                    ApprovalResume(
                        decision=ApprovalDecision(snapshot.status),
                        state_version=snapshot.state_version,
                        decided_by=snapshot.decided_by,
                    ),
                )
            )
        return recovered


@asynccontextmanager
async def open_approval_runner(
    checkpoint_path: str,
    context: ApprovalWorkflowContext,
) -> AsyncIterator[ApprovalRunner]:
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        await saver.setup()
        yield ApprovalRunner(build_approval_graph(saver), context)
