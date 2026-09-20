from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver

from apps.intent_service.runtime import IntentRuntime
from evals.e2e.run import ScriptedLLM, ScriptedRetriever, ScriptedTools
from evals.review_queue import (
    file_sha256,
    verify_ai_assisted_suite,
    verify_human_gold_suite,
)
from evals.tool_selection.build_suite import SUITE as TEMPLATE_SUITE
from packages.agent_core.graph import AgentContext, build_graph, initial_state
from packages.contracts.intent import IntentPrediction

HUMAN_GOLD_SUITE = "tool_selection_1000_v1_human_gold"
AI_ASSISTED_SUITE = "tool_selection_1000_v1_ai_assisted"
SOURCE_SUITE_PATH = Path("evals/tool_selection/tool_selection_1000_v1.jsonl")
HUMAN_GOLD_SUITE_PATH = Path("evals/tool_selection/tool_selection_1000_v1_human_gold.jsonl")
AI_ASSISTED_SUITE_PATH = Path("evals/tool_selection/tool_selection_1000_v1_ai_assisted.jsonl")
AI_ASSISTED_SUMMARY_PATH = Path("reports/eval/tool_selection_1000_v1_ai_assisted_summary.json")
REVIEW_SUMMARY_PATH = Path("reports/eval/tool_selection_1000_v1_review_summary.json")
SUITES = {
    TEMPLATE_SUITE: SOURCE_SUITE_PATH,
    HUMAN_GOLD_SUITE: HUMAN_GOLD_SUITE_PATH,
    AI_ASSISTED_SUITE: AI_ASSISTED_SUITE_PATH,
}


class RuntimeIntentGateway:
    def __init__(self, runtime: IntentRuntime) -> None:
        self.runtime = runtime

    async def predict(self, text: str, previous_user_text: str | None = None) -> IntentPrediction:
        return self.runtime.predict(text, previous_user_text)


def read_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def execute_case(case: dict[str, Any], runtime: IntentRuntime) -> dict[str, Any]:
    scenario = str(case.get("scenario", "normal"))
    tools = ScriptedTools(scenario)
    context = AgentContext(
        intent=RuntimeIntentGateway(runtime),
        llm=ScriptedLLM(scenario),
        retriever=ScriptedRetriever(),
        tools=tools,
    )
    state = initial_state(
        text=str(case["text"]),
        session_id=f"ses_toolsel_{case['case_id']}",
        request_id=f"req_toolsel_{case['case_id']}",
        trace_id=f"tr_toolsel_{case['case_id']}",
        principal_id="usr_demo_0001",
    )
    try:
        result = cast(
            dict[str, Any],
            await build_graph(checkpointer=InMemorySaver()).ainvoke(
                state,
                config={"configurable": {"thread_id": state["session_id"]}},
                context=context,
            ),
        )
        error = None
    except (IndexError, KeyError, ValueError, AssertionError) as caught:
        result = {}
        error = f"{type(caught).__name__}: {caught}"
    expected = [str(value) for value in case["expected_tools"]]
    passed = tools.calls == expected and error is None
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected_tools": expected,
        "actual_tools": tools.calls,
        "predicted_intent": result.get("intent"),
        "actual_route": result.get("route"),
        "actual_status": result.get("status"),
        "error": error,
        "passed": passed,
    }


async def evaluate(cases: list[dict[str, Any]], runtime: IntentRuntime) -> dict[str, Any]:
    rows = [await execute_case(case, runtime) for case in cases]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["category"])].append(row)
    failures = [row for row in rows if not row["passed"]]
    expected_call = [row for row in rows if row["expected_tools"]]
    expected_no_call = [row for row in rows if not row["expected_tools"]]
    failure_reasons = {
        "abstained_without_tool": sum(
            bool(row["expected_tools"]) and not row["actual_tools"] for row in failures
        ),
        "wrong_tool_sequence": sum(
            bool(row["expected_tools"])
            and bool(row["actual_tools"])
            and row["actual_tools"] != row["expected_tools"]
            for row in failures
        ),
        "unexpected_tool_call": sum(
            not row["expected_tools"] and bool(row["actual_tools"]) for row in failures
        ),
        "execution_error": sum(row["error"] is not None for row in failures),
    }
    abstention_statuses: dict[str, int] = defaultdict(int)
    for row in failures:
        if row["expected_tools"] and not row["actual_tools"]:
            abstention_statuses[str(row["actual_status"])] += 1

    def metric(selected: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(bool(row["passed"]) for row in selected)
        return {
            "correct": correct,
            "total": len(selected),
            "accuracy": correct / len(selected) if selected else None,
        }

    return {
        "tool_selection_accuracy": metric(rows),
        "tool_call_cases": metric(expected_call),
        "no_tool_cases": metric(expected_no_call),
        "by_category": {category: metric(values) for category, values in sorted(grouped.items())},
        "failure_reasons": failure_reasons,
        "abstention_statuses": dict(sorted(abstention_statuses.items())),
        "failure_count": len(failures),
        "failure_samples": failures[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate production graph tool selection")
    parser.add_argument("--suite", default=TEMPLATE_SUITE, choices=sorted(SUITES))
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Override the selected suite path without changing its evidence status.",
    )
    parser.add_argument("--runtime", type=Path, default=Path("models/intent_classifier/current"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/eval/tool_selection_1000_v1.json"),
    )
    args = parser.parse_args()
    input_path = args.input or SUITES[args.suite]
    human_gold = args.suite == HUMAN_GOLD_SUITE
    ai_assisted = args.suite == AI_ASSISTED_SUITE
    if human_gold:
        try:
            verify_human_gold_suite(
                source_path=SOURCE_SUITE_PATH,
                adjudicated_path=input_path,
                summary_path=REVIEW_SUMMARY_PATH,
                kind="tool_selection",
                expected_rows=1000,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
    if ai_assisted:
        try:
            verify_ai_assisted_suite(
                source_path=SOURCE_SUITE_PATH,
                adjudicated_path=input_path,
                summary_path=AI_ASSISTED_SUMMARY_PATH,
                kind="tool_selection",
                expected_rows=1000,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
    cases = read_cases(input_path)
    if len(cases) != 1000:
        raise SystemExit(f"tool-selection suite must contain 1,000 cases; found {len(cases)}")
    runtime = IntentRuntime(args.runtime)
    metrics = asyncio.run(evaluate(cases, runtime))
    limitations = [
        ("Dependencies are scripted; the measured boundary is tool selection, not tool execution.")
    ]
    if human_gold:
        limitations.insert(
            0,
            "The expected tool sequences were independently human-adjudicated.",
        )
        limitations.append(
            "Human-gold tool-selection accuracy is not a live production completion metric."
        )
    elif ai_assisted:
        limitations.insert(0, "The project owner accepted AI-assisted tool labels for demo use.")
        limitations.append("This is not independent human-gold or production accuracy.")
    else:
        limitations.insert(
            0,
            "The prompts are deterministic templates and have not been independently reviewed.",
        )
        limitations.append(
            "This diagnostic score must not be described as human-gold or production accuracy."
        )
    report = {
        "schema_version": "1.0",
        "suite": args.suite,
        "suite_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_suite_sha256": file_sha256(SOURCE_SUITE_PATH),
        "suite_status": (
            "human_gold_verified"
            if human_gold
            else "ai_assisted_verified_for_demo"
            if ai_assisted
            else "template_challenge_unreviewed"
        ),
        "evaluation_status": (
            "human_gold_verified"
            if human_gold
            else "ai_assisted_verified_for_demo"
            if ai_assisted
            else "diagnostic_measured"
        ),
        "review_summary": (
            str(REVIEW_SUMMARY_PATH)
            if human_gold
            else str(AI_ASSISTED_SUMMARY_PATH)
            if ai_assisted
            else None
        ),
        "backend": "published_onnx_intent_plus_production_graph_with_scripted_dependencies",
        "model_version": runtime.metadata.model_version,
        "metrics": metrics,
        "limitations": limitations,
        "created_at": datetime.now(UTC).isoformat(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), **metrics["tool_selection_accuracy"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
