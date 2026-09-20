from __future__ import annotations

from pathlib import Path

import pytest

from packages.agent_core.graph import initial_state
from packages.agent_core.runtime import open_agent_runner
from tests.agent_core.test_graph import context


@pytest.mark.asyncio
async def test_async_sqlite_checkpoint_persists_completed_state(tmp_path: Path) -> None:
    checkpoint = tmp_path / "agent-checkpoints.sqlite"
    agent_context, *_ = context()
    state = initial_state(
        text="你好",
        session_id="ses_checkpoint_000001",
        request_id="req_checkpoint_000001",
        trace_id="tr_checkpoint_000001",
        principal_id="usr_demo_0001",
    )
    async with open_agent_runner(str(checkpoint), agent_context) as runner:
        result = await runner.invoke(state)
        snapshot = await runner.graph.aget_state(
            {"configurable": {"thread_id": state["session_id"]}}
        )
    assert result["status"] == "completed"
    assert snapshot.values["final_answer"] == result["final_answer"]
    assert snapshot.values["state_version"] == 1
    assert checkpoint.exists() and checkpoint.stat().st_size > 0


@pytest.mark.asyncio
async def test_new_turn_clears_ephemeral_route_from_same_thread(tmp_path: Path) -> None:
    checkpoint = tmp_path / "multi-turn.sqlite"
    agent_context, fake_intent, *_ = context()
    async with open_agent_runner(str(checkpoint), agent_context) as runner:
        first = initial_state(
            text="完全不支持的问题",
            session_id="ses_checkpoint_turns",
            request_id="req_checkpoint_turn_1",
            trace_id="tr_checkpoint_turn_1",
            principal_id="usr_demo_0001",
        )
        first_result = await runner.invoke(first)
        assert first_result["route"] == "safe_reply"

        second = initial_state(
            text="手机保修政策是什么",
            session_id="ses_checkpoint_turns",
            request_id="req_checkpoint_turn_2",
            trace_id="tr_checkpoint_turn_2",
            principal_id="usr_demo_0001",
            state_version=1,
        )
        second_result = await runner.invoke(second)
    assert second_result["route"] == "knowledge"
    assert second_result["status"] == "completed"
    assert second_result["state_version"] == 2
    assert fake_intent.context_calls[-1] == (
        "手机保修政策是什么",
        "完全不支持的问题",
    )


@pytest.mark.asyncio
async def test_node_budget_degrades_to_explainable_terminal_state(tmp_path: Path) -> None:
    checkpoint = tmp_path / "budget.sqlite"
    from packages.agent_core.contracts import GraphLimits

    agent_context, *_ = context(limits=GraphLimits(max_nodes=5))
    state = initial_state(
        text="手机保修政策是什么",
        session_id="ses_budget_000001",
        request_id="req_budget_000001",
        trace_id="tr_budget_000001",
        principal_id="usr_demo_0001",
    )
    async with open_agent_runner(str(checkpoint), agent_context) as runner:
        result = await runner.invoke(state)
    assert result["status"] == "budget_exceeded"
    assert isinstance(result["final_answer"], str)
    assert "安全执行预算" in result["final_answer"]
    assert "node budget exceeded" in result["errors"][-1]
