from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import Field

from apps.api.settings import Settings
from packages.contracts.base import StrictModel


class ExpressionJudgment(StrictModel):
    relevant: bool
    relevance_score: int = Field(ge=1, le=5)
    concise: bool
    clear: bool
    rationale: str = Field(min_length=1, max_length=300)


def judge_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case in report.get("cases", []):
        target = case["turns"][-1]
        items.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "user_text": target["expected"]["text"],
                "expected_status": target["expected"]["expected_status"],
                "assistant_answer": target["actual"]["answer"],
            }
        )
    return items


def system_prompt() -> str:
    return (
        "你是电商客服回答表达质量评审。只评价回答是否直接回应用户、表达清晰、简洁。"
        "不要评价路由、工具选择、数据库事实、引用真实性或隐藏推理。"
        "若回答是合理的澄清、失败说明、安全拒答或人工转接，也可判为相关。"
        "只输出 JSON 对象，字段必须为 relevant(bool)、relevance_score(1-5整数)、"
        "concise(bool)、clear(bool)、rationale(不超过300字)。"
    )


async def judge_one(
    client: AsyncOpenAI,
    *,
    model: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": json.dumps(item, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=500,
        extra_body={"thinking": {"type": "disabled"}},
    )
    raw = response.choices[0].message.content or "{}"
    judgment = ExpressionJudgment.model_validate_json(raw)
    usage = response.usage
    return {
        **item,
        **judgment.model_dump(),
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
    }


def blocked_result(
    *, model: str, reason: str, error: Exception | None = None
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "status": "not_run",
        "scope": "expression_relevance_only",
        "model": model,
        "reason": reason,
        "judged": 0,
        "total": 360,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if isinstance(error, APIStatusError):
        value["http_status"] = error.status_code
        value["error_type"] = type(error).__name__
    elif error is not None:
        value["error_type"] = type(error).__name__
    return value


async def run_judge(
    report: dict[str, Any],
    *,
    settings: Settings,
    max_cases: int | None = None,
) -> dict[str, Any]:
    items = judge_items(report)
    if len(items) != 360:
        return blocked_result(
            model=settings.deepseek_model,
            reason=f"e2e report must contain 360 case outputs; found {len(items)}",
        )
    secret = settings.deepseek_api_key
    if secret is None or not secret.get_secret_value().strip():
        return blocked_result(
            model=settings.deepseek_model,
            reason="DEEPSEEK_API_KEY is not configured.",
        )
    api_key = secret.get_secret_value()
    selected = items[:max_cases] if max_cases is not None else items
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_seconds,
        max_retries=0,
    )
    rows: list[dict[str, Any]] = []
    try:
        for item in selected:
            rows.append(await judge_one(client, model=settings.deepseek_model, item=item))
    except APIStatusError as error:
        reason = (
            "DeepSeek API balance is insufficient."
            if error.status_code == 402
            else "DeepSeek API rejected the Judge request."
        )
        return blocked_result(model=settings.deepseek_model, reason=reason, error=error)
    except (APIConnectionError, APITimeoutError, RateLimitError) as error:
        return blocked_result(
            model=settings.deepseek_model,
            reason="DeepSeek Judge is unavailable; no score was recorded.",
            error=error,
        )
    relevant = sum(bool(row["relevant"]) for row in rows)
    result = {
        "status": "verified" if len(rows) == len(items) else "partial_not_release_evidence",
        "scope": "expression_relevance_only",
        "model": settings.deepseek_model,
        "judged": len(rows),
        "total": len(items),
        "relevant": relevant,
        "relevance_rate": relevant / len(rows) if rows else None,
        "mean_relevance_score": (
            sum(int(row["relevance_score"]) for row in rows) / len(rows) if rows else None
        ),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "rows": rows,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return result


def update_e2e_report(path: Path, judge: dict[str, Any]) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    report["judge"] = judge
    manual_status = report.get("manual_review", {}).get("status")
    if judge.get("status") == "verified" and manual_status == "human_review_verified":
        report["evaluation_status"] = "verified"
    elif judge.get("status") == "verified":
        report["evaluation_status"] = "judge_verified_manual_review_pending"
    else:
        report["evaluation_status"] = "deterministic_only"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Judge e2e answer expression relevance with DeepSeek"
    )
    parser.add_argument(
        "--e2e-report", type=Path, default=Path("reports/eval/e2e_360_v1.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("reports/eval/e2e_360_v1_judge.json")
    )
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    report = json.loads(args.e2e_report.read_text(encoding="utf-8"))
    result = asyncio.run(run_judge(report, settings=Settings(), max_cases=args.max_cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    update_e2e_report(args.e2e_report, result)
    summary = {key: value for key, value in result.items() if key != "rows"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result["status"] == "not_run":
        raise SystemExit(2)
    if result["status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
