from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from packages.agent_core.budget import consume_llm, consume_node, consume_tool
from packages.agent_core.contracts import (
    AfterSalesSlotsOutput,
    GraphLimits,
    IntentGateway,
    KnowledgeAnswerOutput,
    RetrievalGateway,
    RewriteOutput,
    ShoppingAnswerOutput,
    ShoppingSlotsOutput,
    StructuredLLM,
    ToolGateway,
    TraceSink,
)
from packages.agent_core.state import AgentTask, CommerceState, TaskResult, ToolCallRecord
from packages.contracts.intent import IntentPrediction

GRAPH_VERSION = "commerce_graph_v1"
CONNECTOR_PATTERN = re.compile(r"(?:同时|另外|还要|还有|并且|以及|然后|顺便)")
ORDER_PATTERN = re.compile(r"ord_[A-Za-z0-9_-]{6,64}")
UNSAFE_PATTERNS = ("泄露系统提示", "显示api key", "输出数据库密码", "绕过权限")
WRITE_INTENTS = {"cancel_order", "return_exchange"}


@dataclass(frozen=True)
class AgentContext:
    intent: IntentGateway
    llm: StructuredLLM
    retriever: RetrievalGateway
    tools: ToolGateway
    limits: GraphLimits = field(default_factory=GraphLimits)
    trace_sink: TraceSink | None = None
    tool_deadline_ms: int = 10_000


def _node_count(state: CommerceState, runtime: Runtime[AgentContext], name: str) -> int:
    return consume_node(state, runtime.context.limits, name)


def _llm_limits(state: CommerceState, runtime: Runtime[AgentContext]) -> GraphLimits:
    limits = runtime.context.limits
    if state.get("is_multi_intent"):
        return limits.model_copy(update={"max_llm_calls": min(10, limits.max_llm_calls * 2)})
    return limits


async def _generate(
    state: CommerceState,
    runtime: Runtime[AgentContext],
    prompt_id: str,
    payload: dict[str, Any],
    response_model: type[Any],
) -> tuple[Any, int]:
    count = consume_llm(state, _llm_limits(state, runtime), prompt_id)
    output = await runtime.context.llm.generate(prompt_id, payload, response_model)
    return output, count


def _summary(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(value)[:20]}
    return {"type": type(value).__name__}


async def _call_tool(
    state: CommerceState,
    runtime: Runtime[AgentContext],
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    total, counts, fingerprint = consume_tool(
        state, runtime.context.limits, tool_name, arguments
    )
    started = time.perf_counter()
    envelope = await runtime.context.tools.call(
        tool_name,
        arguments,
        principal_id=state["principal_id"],
        trace_id=state["trace_id"],
        request_id=state["request_id"],
        deadline_ms=runtime.context.tool_deadline_ms,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    ok = envelope.get("ok") is True
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    record = ToolCallRecord(
        tool_name=tool_name,
        fingerprint=fingerprint,
        ok=ok,
        duration_ms=duration_ms,
        error_code=cast(dict[str, Any], error).get("code"),
        result_summary=_summary(envelope.get("data")),
    )
    updates = {
        "tool_call_count": total,
        "tool_call_counts": counts,
        "tool_calls": [*state.get("tool_calls", []), record],
    }
    return envelope, updates


def _task_from_prediction(task_id: str, text: str, value: IntentPrediction) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        text=text,
        intent=value.label.value,
        route=value.route.value,
        confidence=value.confidence,
        read_only=value.label.value not in WRITE_INTENTS,
        decision=value.decision,
    )


def _current_text(state: CommerceState) -> str:
    task = state.get("current_task")
    return str(task.get("text", "")) if task else state.get("normalized_text", "")


def _previous_human_text(state: CommerceState) -> str | None:
    explicit = state.get("previous_user_text")
    if explicit:
        return explicit
    human_texts = [
        str(message.content).strip()
        for message in state.get("messages", [])
        if isinstance(message, HumanMessage) and str(message.content).strip()
    ]
    return human_texts[-2] if len(human_texts) >= 2 else None


async def preprocess(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "preprocess")
    if state.get("graph_version") != GRAPH_VERSION:
        return {
            "node_count": count,
            "status": "incompatible_checkpoint",
            "decision": "safe_reply",
            "final_answer": "会话版本已升级，请新建会话后重试。",
        }
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", state.get("input_text", ""))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {
            "node_count": count,
            "status": "needs_clarification",
            "decision": "clarify",
            "final_answer": "请告诉我你想查询的商品、订单或售后问题。",
        }
    if len(text) > 4000:
        text = text[:4000]
    return {
        "node_count": count,
        "normalized_text": text,
        "previous_user_text": _previous_human_text(state),
        "status": "running",
    }


async def safety_gate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "safety_gate")
    normalized = state.get("normalized_text", "").casefold().replace(" ", "")
    if any(pattern in normalized for pattern in UNSAFE_PATTERNS):
        return {
            "node_count": count,
            "decision": "safe_reply",
            "route": "safe_reply",
            "status": "safe_reply",
            "final_answer": "我不能提供密钥、隐藏提示或绕过权限的方法。",
            "risk_level": "high",
        }
    return {"node_count": count, "risk_level": "low"}


def after_preprocess(state: CommerceState) -> str:
    if state.get("decision") == "clarify":
        return "clarify"
    if state.get("decision") == "safe_reply":
        return "safe_reply"
    return "safety"


def after_safety(state: CommerceState) -> str:
    return "safe_reply" if state.get("decision") == "safe_reply" else "classify"


async def intent_classify(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "intent_classify")
    text = state["normalized_text"]
    prediction = await runtime.context.intent.predict(text, state.get("previous_user_text"))
    predictions: list[tuple[str, IntentPrediction]] = []
    if prediction.is_multi_intent:
        clauses = [part.strip(" ，,。；;") for part in CONNECTOR_PATTERN.split(text)]
        clauses = [part for part in clauses if part][:2]
        for clause in clauses:
            predictions.append((clause, await runtime.context.intent.predict(clause, None)))
    if not predictions:
        predictions = [(text, prediction)]
    tasks = [
        _task_from_prediction(f"task_{index}", clause, item)
        for index, (clause, item) in enumerate(predictions, start=1)
    ]
    tasks.sort(key=lambda item: (not item.get("read_only", True), item["task_id"]))
    return {
        "node_count": count,
        "intent": prediction.label.value,
        "route": prediction.route.value,
        "intent_confidence": prediction.confidence,
        "intent_candidates": [item.model_dump(mode="json") for item in prediction.candidates],
        "is_multi_intent": len(tasks) > 1,
        "route_source": prediction.route_source,
        "decision": prediction.decision,
        "task_queue": tasks,
    }


async def confidence_gate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "confidence_gate")
    tasks = state.get("task_queue", [])
    decisions = {str(task.get("decision")) for task in tasks}
    if "safe_reply" in decisions:
        return {"node_count": count, "decision": "safe_reply", "status": "safe_reply"}
    if "clarify" in decisions:
        return {
            "node_count": count,
            "decision": "clarify",
            "status": "needs_clarification",
        }
    return {"node_count": count, "decision": "dispatch"}


def after_confidence(state: CommerceState) -> str:
    if state.get("decision") == "safe_reply":
        return "safe_reply"
    if state.get("decision") == "clarify":
        return "clarify"
    return "select_task"


async def clarify(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "clarify")
    candidates = [str(item.get("label", "")) for item in state.get("intent_candidates", [])]
    hint = " 或 ".join(candidates[:2])
    answer = f"我还不能确定你的需求。请补充具体对象或操作{f'（可能是 {hint}）' if hint else ''}。"
    return {"node_count": count, "final_answer": answer, "status": "needs_clarification"}


async def safe_reply(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "safe_reply")
    return {
        "node_count": count,
        "final_answer": state.get("final_answer")
        or "这个问题不在电商客服可处理范围内。你可以询问商品、订单、物流或售后。",
        "status": "safe_reply",
    }


async def select_task(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "select_task")
    queue = list(state.get("task_queue", []))
    if not queue:
        return {"node_count": count, "current_task": None, "status": "completed"}
    task = queue.pop(0)
    return {
        "node_count": count,
        "task_queue": queue,
        "current_task": task,
        "intent": task["intent"],
        "route": task["route"],
        "slots": {},
        "slot_provenance": {},
        "candidate_products": [],
        "inventory_snapshots": [],
        "retrieved_docs": [],
        "citations": [],
        "final_answer": None,
        "status": "running",
    }


def dispatch_task(state: CommerceState) -> str:
    route = state.get("route") or "safe_reply"
    allowed = {"knowledge", "shopping", "order", "after_sales", "human", "general"}
    return route if route in allowed else "safe_reply"


def _knowledge_doc_types(intent: str | None) -> list[str]:
    if intent == "product_detail":
        return ["faq", "product"]
    return ["policy", "activity"]


async def knowledge_rewrite(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "knowledge_rewrite")
    output, llm_count = await _generate(
        state,
        runtime,
        "knowledge_rewrite_v1",
        {"query": _current_text(state), "intent": state.get("intent")},
        RewriteOutput,
    )
    value = cast(RewriteOutput, output)
    allowed = set(_knowledge_doc_types(state.get("intent")))
    doc_types = [item for item in value.doc_types if item in allowed] or sorted(allowed)
    return {
        "node_count": count,
        "llm_call_count": llm_count,
        "slots": {**state.get("slots", {}), "rewritten_query": value.query, "doc_types": doc_types},
    }


async def knowledge_retrieve(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "knowledge_retrieve")
    slots = state.get("slots", {})
    result = await runtime.context.retriever.retrieve(
        str(slots.get("rewritten_query", _current_text(state))),
        doc_types=cast(list[str], slots.get("doc_types")),
    )
    return {
        "node_count": count,
        "retrieved_docs": [item.model_dump(mode="json") for item in result.hits],
        "citations": [item.model_dump(mode="json") for item in result.citations],
        "status": "insufficient_evidence" if result.status != "ok" else "running",
    }


def evidence_route(state: CommerceState) -> str:
    return "generate" if state.get("retrieved_docs") else "insufficient"


async def evidence_gate_node(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    return {"node_count": _node_count(state, runtime, "evidence_gate")}


async def knowledge_insufficient(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "knowledge_insufficient")
    return {
        "node_count": count,
        "status": "insufficient_evidence",
        "final_answer": "当前知识库没有足够证据回答这个问题，我不会猜测。",
    }


async def knowledge_generate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "knowledge_generate")
    evidence = [
        {
            "chunk_id": item["chunk_id"],
            "title": item["title"],
            "text": item["text"],
            "knowledge_version": item["knowledge_version"],
        }
        for item in state.get("retrieved_docs", [])
    ]
    output, llm_count = await _generate(
        state,
        runtime,
        "knowledge_answer_v1",
        {"query": _current_text(state), "evidence": evidence},
        KnowledgeAnswerOutput,
    )
    value = cast(KnowledgeAnswerOutput, output)
    return {
        "node_count": count,
        "llm_call_count": llm_count,
        "final_answer": value.answer,
        "slots": {**state.get("slots", {}), "generated_citation_ids": value.citation_ids},
    }


async def knowledge_citation_validate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "knowledge_citation_validate")
    allowed = {str(item["chunk_id"]) for item in state.get("retrieved_docs", [])}
    requested = set(state.get("slots", {}).get("generated_citation_ids", []))
    if not requested or not requested.issubset(allowed):
        return {
            "node_count": count,
            "status": "validation_failed",
            "final_answer": "生成内容的引用无法验证，本次不返回未经证实的答案。",
            "citations": [],
            "errors": [*state.get("errors", []), "citation_validation_failed"],
        }
    citations = [
        item for item in state.get("citations", []) if item.get("chunk_id") in requested
    ]
    return {"node_count": count, "status": "task_completed", "citations": citations}


async def shopping_extract(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_extract")
    output, llm_count = await _generate(
        state,
        runtime,
        "shopping_slots_v1",
        {"query": _current_text(state)},
        ShoppingSlotsOutput,
    )
    value = cast(ShoppingSlotsOutput, output)
    slots = value.model_dump(exclude_none=True)
    return {
        "node_count": count,
        "llm_call_count": llm_count,
        "slots": slots,
        "slot_provenance": {key: "deepseek:shopping_slots_v1" for key in slots},
    }


async def shopping_search(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_search")
    slots = state.get("slots", {})
    arguments = {
        key: value
        for key, value in {
            "query": slots.get("query", _current_text(state)),
            "category": slots.get("category"),
            "brand": slots.get("brand"),
            "price_min": slots.get("price_min"),
            "price_max": slots.get("price_max"),
            "limit": 5,
        }.items()
        if value is not None
    }
    envelope, updates = await _call_tool(state, runtime, "search_products", arguments)
    products = envelope.get("data") if envelope.get("ok") else []
    return {
        "node_count": count,
        **updates,
        "candidate_products": products if isinstance(products, list) else [],
        "status": "running" if products else "insufficient_evidence",
    }


def shopping_found(state: CommerceState) -> str:
    return "inventory" if state.get("candidate_products") else "empty"


async def shopping_empty(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_empty")
    return {
        "node_count": count,
        "final_answer": "没有找到满足条件的在售商品，请放宽品牌、价格或类目条件。",
        "status": "insufficient_evidence",
    }


async def shopping_inventory(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_inventory")
    product = state["candidate_products"][0]
    arguments = {
        "product_id": product["product_id"],
        "sku_id": product["sku_id"],
        "region": state.get("slots", {}).get("region", "北京"),
    }
    envelope, updates = await _call_tool(state, runtime, "check_inventory", arguments)
    snapshot = envelope.get("data") if envelope.get("ok") else None
    return {
        "node_count": count,
        **updates,
        "inventory_snapshots": [snapshot] if isinstance(snapshot, dict) else [],
    }


async def shopping_retrieve(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_retrieve")
    ids = {str(item["product_id"]) for item in state.get("candidate_products", [])}
    result = await runtime.context.retriever.retrieve(
        _current_text(state), doc_types=["product"], candidate_product_ids=ids
    )
    return {
        "node_count": count,
        "retrieved_docs": [item.model_dump(mode="json") for item in result.hits],
        "citations": [item.model_dump(mode="json") for item in result.citations],
    }


async def shopping_generate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_generate")
    output, llm_count = await _generate(
        state,
        runtime,
        "shopping_answer_v1",
        {
            "query": _current_text(state),
            "products": state.get("candidate_products", []),
            "inventory": state.get("inventory_snapshots", []),
            "evidence": state.get("retrieved_docs", []),
        },
        ShoppingAnswerOutput,
    )
    value = cast(ShoppingAnswerOutput, output)
    return {
        "node_count": count,
        "llm_call_count": llm_count,
        "final_answer": value.answer,
        "slots": {
            **state.get("slots", {}),
            "generated_product_ids": value.product_ids,
            "generated_citation_ids": value.citation_ids,
        },
    }


async def shopping_validate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "shopping_validate")
    candidate_ids = {str(item["product_id"]) for item in state.get("candidate_products", [])}
    generated_ids = set(state.get("slots", {}).get("generated_product_ids", []))
    citation_ids = set(state.get("slots", {}).get("generated_citation_ids", []))
    available_citations = {str(item["chunk_id"]) for item in state.get("retrieved_docs", [])}
    if not generated_ids.issubset(candidate_ids) or not citation_ids.issubset(available_citations):
        return {
            "node_count": count,
            "status": "validation_failed",
            "final_answer": "商品比较结果包含候选集外内容或无效引用，本次已阻止输出。",
            "errors": [*state.get("errors", []), "candidate_validation_failed"],
        }
    return {"node_count": count, "status": "task_completed"}


async def order_extract(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "order_extract")
    match = ORDER_PATTERN.search(_current_text(state))
    slots = dict(state.get("slots", {}))
    if match:
        slots["order_id"] = match.group(0)
    return {"node_count": count, "slots": slots}


async def order_choose(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "order_choose")
    if state.get("slots", {}).get("order_id"):
        return {"node_count": count}
    envelope, updates = await _call_tool(state, runtime, "list_recent_orders", {"limit": 3})
    data = envelope.get("data") if envelope.get("ok") else None
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if not orders:
        return {
            "node_count": count,
            **updates,
            "status": "needs_clarification",
            "final_answer": "没有找到可选择的近期订单，请提供订单号。",
        }
    if len(orders) > 1:
        options = "、".join(str(item["order_id"]) for item in orders)
        return {
            "node_count": count,
            **updates,
            "status": "needs_clarification",
            "final_answer": f"请确认要查询哪个订单：{options}。",
        }
    return {
        "node_count": count,
        **updates,
        "slots": {**state.get("slots", {}), "order_id": orders[0]["order_id"]},
    }


def order_selected(state: CommerceState) -> str:
    return "lookup" if state.get("slots", {}).get("order_id") else "missing"


async def order_missing_passthrough(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    return {"node_count": _node_count(state, runtime, "order_missing")}


async def order_lookup(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "order_lookup")
    tool = "track_logistics" if state.get("intent") == "logistics_tracking" else "get_order_detail"
    envelope, updates = await _call_tool(
        state, runtime, tool, {"order_id": state["slots"]["order_id"]}
    )
    data = envelope.get("data") if envelope.get("ok") else None
    if not isinstance(data, dict):
        error = envelope.get("error", {})
        return {
            "node_count": count,
            **updates,
            "status": "tool_failed",
            "final_answer": str(error.get("message", "订单工具调用失败。")),
        }
    return {"node_count": count, **updates, "slots": {**state["slots"], "order_fact": data}}


async def order_format(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "order_format")
    fact = state.get("slots", {}).get("order_fact")
    if not isinstance(fact, dict):
        return {"node_count": count, "status": state.get("status", "tool_failed")}
    if "events" in fact:
        events = fact.get("events", [])
        latest = events[-1] if events else {}
        answer = (
            f"订单 {fact['order_id']} 当前状态为 {fact['order_status']}；"
            f"最新物流：{latest.get('description', '暂无')}。"
        )
    else:
        answer = (
            f"订单 {fact['order_id']} 当前状态为 {fact['status']}，"
            f"共 {len(fact.get('items', []))} 个明细，"
            f"总金额 {fact['total_amount']} {fact['currency']}。"
        )
    return {"node_count": count, "final_answer": answer, "status": "task_completed"}


async def after_sales_extract(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "after_sales_extract")
    output, llm_count = await _generate(
        state,
        runtime,
        "after_sales_slots_v1",
        {"query": _current_text(state)},
        AfterSalesSlotsOutput,
    )
    value = cast(AfterSalesSlotsOutput, output)
    slots = value.model_dump(exclude_none=True)
    return {"node_count": count, "llm_call_count": llm_count, "slots": slots}


def after_sales_ready(state: CommerceState) -> str:
    required = {"order_id", "item_id", "request_type", "reason_code"}
    return "eligibility" if required.issubset(state.get("slots", {})) else "missing"


async def after_sales_missing(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "after_sales_missing")
    required = {"order_id", "item_id", "request_type", "reason_code"}
    missing = sorted(required - set(state.get("slots", {})))
    return {
        "node_count": count,
        "status": "needs_clarification",
        "final_answer": "办理售后前还需要：" + "、".join(missing) + "。",
    }


async def after_sales_eligibility(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "after_sales_eligibility")
    slots = state["slots"]
    arguments = {key: slots[key] for key in ("order_id", "item_id", "request_type", "reason_code")}
    envelope, updates = await _call_tool(
        state, runtime, "check_after_sales_eligibility", arguments
    )
    data = envelope.get("data") if envelope.get("ok") else None
    if not isinstance(data, dict):
        error = envelope.get("error", {})
        return {
            "node_count": count,
            **updates,
            "status": "tool_failed",
            "final_answer": str(error.get("message", "售后资格校验失败。")),
        }
    return {
        "node_count": count,
        **updates,
        "slots": {**slots, "eligibility": data},
        "risk_level": str(data.get("risk_level", "medium")),
    }


async def after_sales_explain(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "after_sales_explain")
    eligibility = state.get("slots", {}).get("eligibility")
    if not isinstance(eligibility, dict):
        return {"node_count": count, "status": state.get("status", "tool_failed")}
    if not eligibility.get("eligible"):
        return {
            "node_count": count,
            "status": "task_completed",
            "final_answer": f"当前不符合自动售后条件：{eligibility.get('reason', '规则未通过')}。",
        }
    materials = "、".join(eligibility.get("required_materials", [])) or "无需额外材料"
    return {
        "node_count": count,
        "status": "awaiting_user_confirmation",
        "final_answer": (
            f"资格校验通过，规则 {eligibility.get('rule_code')}；需要材料：{materials}。"
            "请确认后再创建工单草稿，本步骤尚未执行写操作。"
        ),
        "risk_level": "high",
    }


async def human_route(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "human_route")
    return {
        "node_count": count,
        "status": "human_handoff",
        "final_answer": "已记录你的人工客服请求；接入队列将在 P7 审批与会话 API 中完成。",
    }


async def general_reply(state: CommerceState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    count = _node_count(state, runtime, "general_reply")
    return {
        "node_count": count,
        "status": "task_completed",
        "final_answer": "你好，我可以帮助查询商品、库存、订单、物流和售后规则。",
    }


async def task_safe_reply(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "task_safe_reply")
    return {
        "node_count": count,
        "status": "safe_reply",
        "final_answer": "当前任务不在可执行的电商服务范围内。",
    }


async def complete_task(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "complete_task")
    task = state.get("current_task") or AgentTask(task_id="task_unknown", route="unknown")
    result = TaskResult(
        task_id=str(task.get("task_id")),
        route=str(task.get("route")),
        status=state.get("status", "completed"),
        answer=state.get("final_answer") or "该任务未生成答案。",
    )
    return {
        "node_count": count,
        "task_results": [*state.get("task_results", []), result],
        "current_task": None,
    }


def after_task(state: CommerceState) -> str:
    return "next" if state.get("task_queue") else "validate"


async def answer_validate(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "answer_validate")
    results = state.get("task_results", [])
    if len(results) > 1:
        answer = "\n\n".join(
            f"{index}. {item.get('answer', '')}" for index, item in enumerate(results, start=1)
        )
    elif results:
        answer = str(results[0].get("answer", ""))
    else:
        answer = state.get("final_answer") or "本次没有生成可返回的结果。"
    terminal = {str(item.get("status")) for item in results}
    status_priority = (
        "awaiting_user_confirmation",
        "needs_clarification",
        "tool_failed",
        "validation_failed",
        "insufficient_evidence",
        "human_handoff",
        "safe_reply",
    )
    status = next(
        (candidate for candidate in status_priority if candidate in terminal),
        state.get("status", "completed") if not results else "completed",
    )
    return {
        "node_count": count,
        "final_answer": answer[:6000],
        "status": status,
        "messages": [AIMessage(content=answer[:6000])],
    }


async def persist_trace(
    state: CommerceState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    count = _node_count(state, runtime, "persist_trace")
    if runtime.context.trace_sink is not None:
        await runtime.context.trace_sink.persist(cast(dict[str, Any], state))
    return {"node_count": count, "state_version": int(state.get("state_version", 0)) + 1}


def _knowledge_graph() -> Any:
    graph = StateGraph(CommerceState, context_schema=AgentContext)
    graph.add_node("rewrite_query", knowledge_rewrite)
    graph.add_node("retrieve", knowledge_retrieve)
    graph.add_node("evidence_gate", evidence_gate_node)
    graph.add_node("generate", knowledge_generate)
    graph.add_node("insufficient", knowledge_insufficient)
    graph.add_node("citation_validate", knowledge_citation_validate)
    graph.add_edge(START, "rewrite_query")
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("retrieve", "evidence_gate")
    graph.add_conditional_edges(
        "evidence_gate", evidence_route, {"generate": "generate", "insufficient": "insufficient"}
    )
    graph.add_edge("generate", "citation_validate")
    graph.add_edge("citation_validate", END)
    graph.add_edge("insufficient", END)
    return graph.compile(name="knowledge_subgraph")


def _shopping_graph() -> Any:
    graph = StateGraph(CommerceState, context_schema=AgentContext)
    graph.add_node("extract_slots", shopping_extract)
    graph.add_node("structured_search", shopping_search)
    graph.add_node("inventory_check", shopping_inventory)
    graph.add_node("empty", shopping_empty)
    graph.add_node("retrieve_candidate_docs", shopping_retrieve)
    graph.add_node("generate_comparison", shopping_generate)
    graph.add_node("candidate_validate", shopping_validate)
    graph.add_edge(START, "extract_slots")
    graph.add_edge("extract_slots", "structured_search")
    graph.add_conditional_edges(
        "structured_search", shopping_found, {"inventory": "inventory_check", "empty": "empty"}
    )
    graph.add_edge("inventory_check", "retrieve_candidate_docs")
    graph.add_edge("retrieve_candidate_docs", "generate_comparison")
    graph.add_edge("generate_comparison", "candidate_validate")
    graph.add_edge("candidate_validate", END)
    graph.add_edge("empty", END)
    return graph.compile(name="shopping_subgraph")


def _order_graph() -> Any:
    graph = StateGraph(CommerceState, context_schema=AgentContext)
    graph.add_node("extract_order", order_extract)
    graph.add_node("choose_recent_order", order_choose)
    graph.add_node("ownership_checked_tool", order_lookup)
    graph.add_node("missing", order_missing_passthrough)
    graph.add_node("format_fact_response", order_format)
    graph.add_edge(START, "extract_order")
    graph.add_edge("extract_order", "choose_recent_order")
    graph.add_conditional_edges(
        "choose_recent_order",
        order_selected,
        {"lookup": "ownership_checked_tool", "missing": "missing"},
    )
    graph.add_edge("ownership_checked_tool", "format_fact_response")
    graph.add_edge("format_fact_response", END)
    graph.add_edge("missing", END)
    return graph.compile(name="order_subgraph")


def _after_sales_graph() -> Any:
    graph = StateGraph(CommerceState, context_schema=AgentContext)
    graph.add_node("extract_request", after_sales_extract)
    graph.add_node("missing", after_sales_missing)
    graph.add_node("eligibility_tool", after_sales_eligibility)
    graph.add_node("explain_or_confirm", after_sales_explain)
    graph.add_edge(START, "extract_request")
    graph.add_conditional_edges(
        "extract_request",
        after_sales_ready,
        {"eligibility": "eligibility_tool", "missing": "missing"},
    )
    graph.add_edge("eligibility_tool", "explain_or_confirm")
    graph.add_edge("explain_or_confirm", END)
    graph.add_edge("missing", END)
    return graph.compile(name="after_sales_subgraph")


def build_graph(checkpointer: Any | None = None) -> Any:
    graph = StateGraph(CommerceState, context_schema=AgentContext)
    graph.add_node("preprocess", preprocess)
    graph.add_node("safety_and_multi_intent_gate", safety_gate)
    graph.add_node("intent_classify", intent_classify)
    graph.add_node("confidence_gate", confidence_gate)
    graph.add_node("clarify", clarify)
    graph.add_node("safe_reply", safe_reply)
    graph.add_node("select_task", select_task)
    graph.add_node("knowledge", _knowledge_graph())
    graph.add_node("shopping", _shopping_graph())
    graph.add_node("order", _order_graph())
    graph.add_node("after_sales", _after_sales_graph())
    graph.add_node("human", human_route)
    graph.add_node("general", general_reply)
    graph.add_node("task_safe_reply", task_safe_reply)
    graph.add_node("complete_task", complete_task)
    graph.add_node("answer_validate", answer_validate)
    graph.add_node("persist_trace", persist_trace)

    graph.add_edge(START, "preprocess")
    graph.add_conditional_edges(
        "preprocess",
        after_preprocess,
        {
            "safety": "safety_and_multi_intent_gate",
            "clarify": "clarify",
            "safe_reply": "safe_reply",
        },
    )
    graph.add_conditional_edges(
        "safety_and_multi_intent_gate",
        after_safety,
        {"classify": "intent_classify", "safe_reply": "safe_reply"},
    )
    graph.add_edge("intent_classify", "confidence_gate")
    graph.add_conditional_edges(
        "confidence_gate",
        after_confidence,
        {"clarify": "clarify", "safe_reply": "safe_reply", "select_task": "select_task"},
    )
    graph.add_edge("clarify", "answer_validate")
    graph.add_edge("safe_reply", "answer_validate")
    graph.add_conditional_edges(
        "select_task",
        dispatch_task,
        {
            "knowledge": "knowledge",
            "shopping": "shopping",
            "order": "order",
            "after_sales": "after_sales",
            "human": "human",
            "general": "general",
            "safe_reply": "task_safe_reply",
        },
    )
    task_nodes = (
        "knowledge",
        "shopping",
        "order",
        "after_sales",
        "human",
        "general",
        "task_safe_reply",
    )
    for node in task_nodes:
        graph.add_edge(node, "complete_task")
    graph.add_conditional_edges(
        "complete_task", after_task, {"next": "select_task", "validate": "answer_validate"}
    )
    graph.add_edge("answer_validate", "persist_trace")
    graph.add_edge("persist_trace", END)
    return graph.compile(checkpointer=checkpointer, name=GRAPH_VERSION)


def initial_state(
    *,
    text: str,
    session_id: str,
    request_id: str,
    trace_id: str,
    principal_id: str,
    state_version: int = 0,
) -> CommerceState:
    return CommerceState(
        session_id=session_id,
        request_id=request_id,
        trace_id=trace_id,
        principal_id=principal_id,
        graph_version=GRAPH_VERSION,
        state_version=state_version,
        messages=[HumanMessage(content=text)],
        input_text=text,
        normalized_text="",
        previous_user_text=None,
        intent=None,
        intent_candidates=[],
        intent_confidence=None,
        is_multi_intent=False,
        route=None,
        route_source=None,
        decision=None,
        slots={},
        slot_provenance={},
        task_queue=[],
        current_task=None,
        task_results=[],
        candidate_products=[],
        inventory_snapshots=[],
        retrieved_docs=[],
        citations=[],
        tool_calls=[],
        tool_call_counts={},
        retry_counters={},
        risk_level="low",
        pending_approval_id=None,
        node_count=0,
        llm_call_count=0,
        tool_call_count=0,
        status="created",
        final_answer=None,
        errors=[],
    )
