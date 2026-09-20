from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import InMemorySaver

from evals.e2e.build_suite import SUITE_NAME
from packages.agent_core.contracts import (
    AfterSalesSlotsOutput,
    KnowledgeAnswerOutput,
    RewriteOutput,
    ShoppingAnswerOutput,
    ShoppingSlotsOutput,
)
from packages.agent_core.graph import GRAPH_VERSION, AgentContext, build_graph, initial_state
from packages.contracts.intent import (
    HighLevelRoute,
    IntentCandidate,
    IntentLabel,
    IntentPrediction,
)
from packages.rag_core.models import Citation, RetrievalHit, RetrievalResult


class ScriptedIntent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def predict(
        self, text: str, previous_user_text: str | None = None
    ) -> IntentPrediction:
        self.calls.append((text, previous_user_text))
        multi = "同时" in text
        if "退货" in text:
            label, route = IntentLabel.RETURN_EXCHANGE, HighLevelRoute.AFTER_SALES
        elif "物流" in text:
            label, route = IntentLabel.LOGISTICS_TRACKING, HighLevelRoute.ORDER
        elif "订单" in text:
            label, route = IntentLabel.ORDER_STATUS, HighLevelRoute.ORDER
        elif "推荐" in text:
            label, route = IntentLabel.PRODUCT_RECOMMEND, HighLevelRoute.SHOPPING
        elif "政策" in text or "保修" in text:
            label, route = IntentLabel.POLICY_FAQ, HighLevelRoute.KNOWLEDGE
        elif "转人工" in text:
            label, route = IntentLabel.HUMAN_HANDOFF, HighLevelRoute.HUMAN
        elif "你好" in text:
            label, route = IntentLabel.CHITCHAT, HighLevelRoute.GENERAL
        else:
            return self._prediction(
                IntentLabel.OUT_OF_SCOPE,
                HighLevelRoute.SAFE_REPLY,
                multi=multi,
                decision="safe_reply",
            )
        return self._prediction(label, route, multi=multi)

    @staticmethod
    def _prediction(
        label: IntentLabel,
        route: HighLevelRoute,
        *,
        multi: bool = False,
        decision: Literal["auto_route", "clarify", "safe_reply"] = "auto_route",
    ) -> IntentPrediction:
        return IntentPrediction(
            label=label,
            route=route,
            confidence=0.99,
            candidates=[
                IntentCandidate(label=label, probability=0.99),
                IntentCandidate(label=IntentLabel.OUT_OF_SCOPE, probability=0.01),
            ],
            is_multi_intent=multi,
            model_version="scripted-e2e-v1",
            decision=decision,
            margin=0.98,
            route_source="scripted_eval_gateway",
        )


class ScriptedLLM:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    async def generate(
        self, prompt_id: str, payload: dict[str, Any], response_model: type[Any]
    ) -> Any:
        value: dict[str, Any]
        model: type[Any]
        if prompt_id == "knowledge_rewrite_v1":
            value = {"query": payload["query"], "doc_types": ["policy"]}
            model = RewriteOutput
        elif prompt_id == "knowledge_answer_v1":
            citation = (
                "chk_not_in_evidence"
                if self.scenario == "invalid_citation"
                else payload["evidence"][0]["chunk_id"]
            )
            value = {"answer": "根据现行政策，可以在规则窗口内申请。", "citation_ids": [citation]}
            model = KnowledgeAnswerOutput
        elif prompt_id == "shopping_slots_v1":
            value = {"query": "耳机", "price_max": 500, "region": "北京"}
            model = ShoppingSlotsOutput
        elif prompt_id == "shopping_answer_v1":
            product_id = (
                "prd_outside_candidate_set"
                if self.scenario == "invalid_product"
                else payload["products"][0]["product_id"]
            )
            value = {
                "answer": "推荐候选集中的耳机。",
                "product_ids": [product_id],
                "citation_ids": [payload["evidence"][0]["chunk_id"]],
            }
            model = ShoppingAnswerOutput
        elif prompt_id == "after_sales_slots_v1":
            if self.scenario == "missing_after_sales_slots":
                value = {"request_type": "return"}
            else:
                value = {
                    "order_id": "ord_demo_000005",
                    "item_id": "item_demo_000005_1",
                    "request_type": "return",
                    "reason_code": "DO_NOT_WANT",
                    "description": "不想要了",
                }
            model = AfterSalesSlotsOutput
        else:
            raise AssertionError(prompt_id)
        if response_model is not model:
            raise AssertionError(f"unexpected response model for {prompt_id}")
        return response_model.model_validate(value)


class ScriptedRetriever:
    async def retrieve(
        self,
        query: str,
        *,
        doc_types: list[str] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> RetrievalResult:
        product = doc_types == ["product"]
        hit = RetrievalHit(
            chunk_id="chk_product_demo_000001_01" if product else "chk_rule_demo_001_01",
            doc_id="doc_product_demo_000001" if product else "doc_rule_demo_001",
            doc_type="product" if product else "policy",
            title="演示证据",
            text="这是可验证的知识证据。",
            source_uri="demo://evidence/1",
            knowledge_version="kb_e2e_v1",
            content_hash="a" * 64,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            product_id="prd_demo_000001" if product else None,
            sku_id="sku_demo_000001" if product else None,
            category="耳机" if product else "手机",
            score=0.99,
        )
        if candidate_product_ids is not None and hit.product_id not in candidate_product_ids:
            raise AssertionError("scripted retriever escaped candidate boundary")
        citation = Citation(
            chunk_id=hit.chunk_id,
            doc_id=hit.doc_id,
            title=hit.title,
            source_uri=hit.source_uri,
            knowledge_version=hit.knowledge_version,
            content_hash=hit.content_hash,
            effective_from=hit.effective_from,
        )
        return RetrievalResult(
            query=query,
            status="ok",
            hits=[hit],
            citations=[citation],
            stages_ms={"total": 1},
            embedding_model="scripted",
            reranker_model="scripted",
            decision_score=0.99,
        )


class ScriptedTools:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[str] = []

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
        trace_id: str,
        request_id: str,
        deadline_ms: int,
    ) -> dict[str, Any]:
        del principal_id, request_id, deadline_ms
        self.calls.append(tool_name)
        failed = {
            "fail_search_products": "search_products",
            "fail_order_lookup": "get_order_detail",
            "fail_after_sales": "check_after_sales_eligibility",
        }.get(self.scenario)
        if failed == tool_name:
            return {
                "ok": False,
                "data": None,
                "error": {"code": "dependency_unavailable", "message": "依赖服务暂时不可用。"},
                "meta": {"trace_id": trace_id},
            }
        if tool_name == "search_products":
            data: Any = [{
                "product_id": "prd_demo_000001",
                "sku_id": "sku_demo_000001",
                "name": "星穹耳机",
                "price": "399.00",
            }]
        elif tool_name == "check_inventory":
            data = {"available_quantity": 8, **arguments}
        elif tool_name == "get_order_detail":
            data = {
                "order_id": arguments["order_id"],
                "status": "paid",
                "items": [{"item_id": "item_demo_000001_1"}],
                "total_amount": "99.00",
                "currency": "CNY",
            }
        elif tool_name == "track_logistics":
            data = {
                "order_id": arguments["order_id"],
                "order_status": "shipped",
                "events": [{"description": "运输中"}],
            }
        elif tool_name == "list_recent_orders":
            data = {"orders": [], "next_cursor": None}
        elif tool_name == "check_after_sales_eligibility":
            data = {
                "eligible": True,
                "rule_code": "RETURN_V1",
                "required_materials": ["商品照片"],
                "reason": "规则通过",
                "risk_level": "high",
            }
        else:
            raise AssertionError(tool_name)
        return {"ok": True, "data": data, "error": None, "meta": {"trace_id": trace_id}}


def read_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _actual_task_routes(state: dict[str, Any]) -> list[str]:
    return [str(item.get("route")) for item in state.get("task_results", [])]


async def execute_case(case: dict[str, Any]) -> dict[str, Any]:
    scenario = str(case["scenario"])
    intent = ScriptedIntent()
    tools = ScriptedTools(scenario)
    context = AgentContext(
        intent=intent,
        llm=ScriptedLLM(scenario),
        retriever=ScriptedRetriever(),
        tools=tools,
    )
    graph = build_graph(checkpointer=InMemorySaver())
    session_id = f"ses_{case['case_id']}"
    previous_version = 0
    rows: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(case["turns"], start=1):
        start_tools = len(tools.calls)
        start_intent_calls = len(intent.calls)
        state = initial_state(
            text=str(turn["text"]),
            session_id=session_id,
            request_id=f"req_{case['case_id']}_{turn_index}",
            trace_id=f"tr_{case['case_id']}_{turn_index}",
            principal_id=str(case["principal_id"]),
            state_version=previous_version,
        )
        result = cast(
            dict[str, Any],
            await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": session_id}},
                context=context,
            ),
        )
        actual_tools = tools.calls[start_tools:]
        actual_task_routes = _actual_task_routes(result)
        errors = [str(item) for item in result.get("errors", [])]
        expected_error = turn.get("expected_error")
        previous_context = (
            intent.calls[start_intent_calls][1]
            if len(intent.calls) > start_intent_calls
            else None
        )
        expected_previous = case["turns"][turn_index - 2]["text"] if turn_index > 1 else None
        checks = {
            "route": result.get("route") == turn["expected_route"],
            "tools": actual_tools == turn["expected_tools"],
            "status": result.get("status") == turn["expected_status"],
            "task_routes": actual_task_routes == turn["expected_task_routes"],
            "state_version": result.get("state_version") == previous_version + 1,
            "previous_user_context": (
                previous_context == expected_previous
                if result.get("route") != "safe_reply" or start_intent_calls < len(intent.calls)
                else True
            ),
            "confirmation": (result.get("status") == "awaiting_user_confirmation")
            == bool(turn["expected_confirmation"]),
            "expected_error": expected_error is None or expected_error in errors,
            "no_write_before_confirmation": all(
                name not in {"create_after_sales_ticket", "cancel_order"} for name in actual_tools
            ),
            "no_tools_for_safety": turn["expected_route"] != "safe_reply" or not actual_tools,
        }
        rows.append(
            {
                "turn": turn_index,
                "expected": turn,
                "actual": {
                    "route": result.get("route"),
                    "status": result.get("status"),
                    "tools": actual_tools,
                    "task_routes": actual_task_routes,
                    "state_version": result.get("state_version"),
                    "previous_user_text": result.get("previous_user_text"),
                    "errors": errors,
                    "answer": result.get("final_answer"),
                },
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
        previous_version = int(result.get("state_version", previous_version))
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "scenario": scenario,
        "passed": all(row["passed"] for row in rows),
        "turns": rows,
    }


async def run_suite(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [await execute_case(case) for case in cases]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result["category"])].append(result)
    failures = [result for result in results if not result["passed"]]
    abnormal = [
        result
        for result in results
        if result["category"] in {"missing_information", "tool_failure", "validation_guard"}
    ]
    manual_queue = [
        {
            "case_id": case["case_id"],
            "category": case["category"],
            "scenario": case["scenario"],
            "review_status": "pending_human_review",
            "reviewer_id": None,
            "reviewed_at": None,
            "relevance_score": None,
            "clarity_score": None,
            "safe_and_helpful": None,
            "verdict": None,
            "notes": None,
            "turns": [
                {
                    "turn": row["turn"],
                    "user_text": row["expected"]["text"],
                    "assistant_answer": row["actual"]["answer"],
                    "route": row["actual"]["route"],
                    "status": row["actual"]["status"],
                    "tools": row["actual"]["tools"],
                }
                for row in results[index]["turns"]
            ],
        }
        for index, case in enumerate(cases)
        if index % 5 == 0
    ]
    metrics = {
        "deterministic_contract": {
            "passed": len(results) - len(failures),
            "total": len(results),
            "accuracy": (len(results) - len(failures)) / len(results),
        },
        "task_completion_rate": {
            "status": "measured_scripted_gateway_contract",
            "completed": len(results) - len(failures),
            "total": len(results),
            "rate": (len(results) - len(failures)) / len(results),
        },
        "abnormal_fallback_rate": {
            "correct": sum(bool(item["passed"]) for item in abnormal),
            "total": len(abnormal),
            "rate": sum(bool(item["passed"]) for item in abnormal) / len(abnormal),
        },
        "by_category": {
            name: {
                "passed": sum(bool(item["passed"]) for item in values),
                "total": len(values),
            }
            for name, values in sorted(grouped.items())
        },
    }
    return {
        "schema_version": "1.0",
        "suite": SUITE_NAME,
        "suite_status": "deterministic_graph_contract_verified" if not failures else "failed",
        "evaluation_status": "deterministic_only",
        "backend": "production_graph_with_scripted_gateways",
        "graph_version": GRAPH_VERSION,
        "case_count": len(results),
        "turn_count": sum(len(item["turns"]) for item in results),
        "abnormal_case_count": len(abnormal),
        "metrics": metrics,
        "judge": {
            "status": "not_run",
            "reason": (
                "DeepSeek Judge was not requested or no verified live API result is available."
            ),
        },
        "manual_review": {
            "required_sample_rate": 0.2,
            "sample_count": len(manual_queue),
            "status": "pending_human_review",
            "queue": manual_queue,
        },
        "cases": results,
        "limitations": [
            (
                "Gateways are deterministic scripted doubles; this is not a live-LLM "
                "production completion rate."
            ),
            "Expression relevance is not scored because DeepSeek Judge was not run.",
            "The 20% manual review queue is exported but not marked reviewed.",
        ],
        "failures": failures,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _review_input_sha256(row: dict[str, Any]) -> str:
    payload = json.dumps(row.get("turns", []), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_manual_queue(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        existing = {
            str(row["case_id"]): row
            for row in read_cases(path)
            if isinstance(row, dict) and row.get("case_id")
        }
    review_fields = (
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "relevance_score",
        "clarity_score",
        "safe_and_helpful",
        "verdict",
        "notes",
    )
    rows: list[dict[str, Any]] = []
    for raw in report["manual_review"]["queue"]:
        row = dict(raw)
        fingerprint = _review_input_sha256(row)
        previous = existing.get(str(row["case_id"]))
        if previous is not None and previous.get("review_input_sha256") == fingerprint:
            for field in review_fields:
                row[field] = previous.get(field)
        row["review_input_sha256"] = fingerprint
        rows.append(row)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic multi-turn Agent graph evaluation"
    )
    parser.add_argument("--suite", default=SUITE_NAME, choices=[SUITE_NAME])
    parser.add_argument("--input", type=Path, default=Path("evals/e2e/e2e_360_v1.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/eval/e2e_360_v1.json"))
    parser.add_argument(
        "--manual-queue",
        type=Path,
        default=Path("reports/eval/e2e_360_v1_manual_review.jsonl"),
    )
    args = parser.parse_args()
    cases = read_cases(args.input)
    if len(cases) != 360:
        raise SystemExit(f"expected 360 cases, found {len(cases)}")
    report = asyncio.run(run_suite(cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_manual_queue(report, args.manual_queue)
    summary = {
        "suite": args.suite,
        "status": report["suite_status"],
        "metrics": report["metrics"],
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
