from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentTask(TypedDict, total=False):
    task_id: str
    text: str
    intent: str
    route: str
    confidence: float
    read_only: bool
    decision: str


class TaskResult(TypedDict, total=False):
    task_id: str
    route: str
    status: str
    answer: str


class ToolCallRecord(TypedDict, total=False):
    tool_name: str
    fingerprint: str
    ok: bool
    duration_ms: int
    error_code: str | None
    result_summary: dict[str, Any]


class CommerceState(TypedDict, total=False):
    session_id: str
    request_id: str
    trace_id: str
    principal_id: str
    graph_version: str
    state_version: int
    messages: Annotated[list[AnyMessage], add_messages]
    input_text: str
    normalized_text: str
    previous_user_text: str | None
    intent: str | None
    intent_candidates: list[dict[str, Any]]
    intent_confidence: float | None
    is_multi_intent: bool
    route: str | None
    route_source: str | None
    decision: str | None
    slots: dict[str, Any]
    slot_provenance: dict[str, str]
    task_queue: list[AgentTask]
    current_task: AgentTask | None
    task_results: list[TaskResult]
    candidate_products: list[dict[str, Any]]
    inventory_snapshots: list[dict[str, Any]]
    retrieved_docs: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    tool_calls: list[ToolCallRecord]
    tool_call_counts: dict[str, int]
    retry_counters: dict[str, int]
    risk_level: Literal["low", "medium", "high"]
    pending_approval_id: str | None
    node_count: int
    llm_call_count: int
    tool_call_count: int
    status: str
    final_answer: str | None
    errors: list[str]
