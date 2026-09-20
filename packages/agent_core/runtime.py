from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from packages.agent_core.budget import BudgetExceeded
from packages.agent_core.graph import AgentContext, build_graph
from packages.agent_core.state import CommerceState


@dataclass
class AgentRunner:
    graph: Any
    context: AgentContext

    async def invoke(self, state: CommerceState) -> CommerceState:
        config = {
            "configurable": {"thread_id": state["session_id"]},
            "recursion_limit": max(25, self.context.limits.max_nodes * 2),
        }
        try:
            result = await self.graph.ainvoke(state, config=config, context=self.context)
            return cast(CommerceState, result)
        except BudgetExceeded as error:
            snapshot = await self.graph.aget_state(config)
            values = cast(CommerceState, dict(snapshot.values))
            values["status"] = "budget_exceeded"
            values["final_answer"] = "本次请求已达到安全执行预算，请缩小问题范围后重试。"
            values["errors"] = [*values.get("errors", []), str(error)]
            if self.context.trace_sink is not None:
                await self.context.trace_sink.persist(cast(dict[str, Any], values))
            return values


@asynccontextmanager
async def open_agent_runner(
    checkpoint_path: str, context: AgentContext
) -> AsyncIterator[AgentRunner]:
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        await saver.setup()
        yield AgentRunner(build_graph(checkpointer=saver), context)
