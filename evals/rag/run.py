from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from packages.business.database import create_engine, session_factory
from packages.business.models import EvaluationResult, EvaluationRun
from packages.rag_core.config import load_rag_config
from packages.rag_core.retriever import HybridRetriever, RetrievalVariant, validate_citations

VARIANTS: tuple[RetrievalVariant, ...] = ("bm25", "vector", "hybrid", "hybrid_rerank")


def percentile(values: list[int], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile_value * len(ordered)) - 1)
    return float(ordered[index])


def summarize(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    answerable = [row for row in rows if not row["expect_no_answer"]]
    no_answer = [row for row in rows if row["expect_no_answer"]]
    latency = [int(row["latency_ms"]) for row in rows]
    return {
        "cases": len(rows),
        "recall_at_5": statistics.fmean(row["recall_at_5"] for row in answerable),
        "mrr": statistics.fmean(row["reciprocal_rank"] for row in answerable),
        "ndcg_at_5": statistics.fmean(row["ndcg_at_5"] for row in answerable),
        "citation_hit_rate": statistics.fmean(row["citation_hit"] for row in answerable),
        "citation_verify_rate": statistics.fmean(
            row["citation_verified"] for row in answerable
        ),
        "no_answer_rejection_rate": statistics.fmean(
            row["rejected"] for row in no_answer
        ),
        "latency_p50_ms": percentile(latency, 0.50),
        "latency_p95_ms": percentile(latency, 0.95),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_rag_config(args.config)
    cases = [json.loads(line) for line in args.suite.read_text(encoding="utf-8").splitlines()]
    retriever = HybridRetriever(config)
    all_rows: list[dict[str, Any]] = []
    completed: set[tuple[str, str]] = set()
    if args.results.exists():
        for line in args.results.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            all_rows.append(row)
            completed.add((str(row["variant"]), str(row["case_id"])))
    args.results.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.results.open("a", encoding="utf-8") as checkpoint:
            for variant in VARIANTS:
                for index, case in enumerate(cases, start=1):
                    key = (variant, str(case["case_id"]))
                    if key not in completed:
                        doc_types = (
                            None if case["expect_no_answer"] else [case["category"]]
                        )
                        result = await retriever.retrieve(
                            case["query"], variant=variant, doc_types=doc_types
                        )
                        returned = [hit.chunk_id for hit in result.hits[:5]]
                        relevant = set(case["relevant_chunk_ids"])
                        rank = next(
                            (
                                rank
                                for rank, chunk_id in enumerate(returned, start=1)
                                if chunk_id in relevant
                            ),
                            None,
                        )
                        row = {
                            "variant": variant,
                            "case_id": case["case_id"],
                            "category": case["category"],
                            "expect_no_answer": case["expect_no_answer"],
                            "returned_chunk_ids": returned,
                            "status": result.status,
                            "decision_score": result.decision_score,
                            "recall_at_5": float(rank is not None),
                            "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
                            "ndcg_at_5": (
                                0.0 if rank is None else 1.0 / math.log2(rank + 1)
                            ),
                            "citation_hit": float(
                                any(item.chunk_id in relevant for item in result.citations)
                            ),
                            "citation_verified": float(validate_citations(result)),
                            "rejected": float(
                                result.status == "insufficient_evidence"
                            ),
                            "latency_ms": result.stages_ms.get("total", 0),
                        }
                        all_rows.append(row)
                        completed.add(key)
                        checkpoint.write(
                            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                        )
                        checkpoint.flush()
                    if index % 50 == 0:
                        print(f"{variant}: {index}/{len(cases)}", flush=True)
    finally:
        await retriever.close()

    metrics = {
        variant: summarize([row for row in all_rows if row["variant"] == variant])
        for variant in VARIANTS
    }
    report = {
        "status": "completed",
        "suite": config.evaluation.suite,
        "knowledge_version": config.knowledge_version,
        "completed_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "result_count": len(all_rows),
        "results": all_rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    run_id = f"eval_{config.evaluation.suite}"
    manifest_hash = hashlib.sha256(args.suite.read_bytes()).hexdigest()
    engine = create_engine()
    factory = session_factory(engine)
    async with factory() as session:
        await session.execute(
            delete(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id)
        )
        await session.execute(delete(EvaluationRun).where(EvaluationRun.id == run_id))
        session.add(
            EvaluationRun(
                id=run_id,
                name=f"{config.evaluation.suite} four-way retrieval comparison",
                manifest_sha256=manifest_hash,
                status="completed",
                metrics_json=metrics,
                created_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        await session.flush()
        for index, row in enumerate(all_rows, start=1):
            passed = bool(row["rejected"] if row["expect_no_answer"] else row["recall_at_5"])
            session.add(
                EvaluationResult(
                    id=f"evr_{index:05d}",
                    evaluation_run_id=run_id,
                    case_id=f"{row['variant']}:{row['case_id']}",
                    category=row["category"],
                    passed=passed,
                    score=round(float(row["reciprocal_rank"]), 5),
                    error_type=None if passed else "retrieval_miss",
                    details_json=row,
                )
            )
        await session.commit()
    await engine.dispose()
    return {key: value for key, value in report.items() if key != "results"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the four-way 600-case RAG evaluation")
    parser.add_argument("--config", default="configs/rag/rag_v1.yaml")
    parser.add_argument("--suite", type=Path, default=Path("evals/rag/rag_v1.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("reports/eval/rag_v1.json"))
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("reports/eval/rag_v1_results.jsonl"),
    )
    return parser.parse_args()


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
