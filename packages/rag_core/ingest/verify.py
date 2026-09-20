from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.business.database import create_engine, session_factory
from packages.rag_core.config import physical_name
from packages.rag_core.ingest.common import add_common_arguments, config_from_args
from packages.rag_core.pipeline import load_manifest, update_version_status
from packages.rag_core.retriever import HybridRetriever, validate_citations
from packages.rag_core.stores import ElasticsearchStore, MilvusStore

SMOKE_QUERIES = [
    ("星穹耳机适合通勤吗", ["faq", "product"]),
    ("SKU 000001 的静态规格", ["product"]),
    ("蓝牙耳机保修多久", ["faq", "product"]),
    ("智能手表适合运动吗", ["faq", "product"]),
    ("笔记本电脑有哪些使用场景", ["faq", "product"]),
    ("手机的退货政策", ["policy"]),
    ("退货需要哪些材料", ["policy"]),
    ("商品未拆封能否退货", ["policy"]),
    ("包装破损如何申请售后", ["policy"]),
    ("质量问题换货规则", ["policy"]),
    ("平板电脑静态参数", ["product"]),
    ("机械键盘适合办公吗", ["faq", "product"]),
    ("显示器适用人群", ["faq", "product"]),
    ("路由器产品说明", ["faq", "product"]),
    ("相机保修说明", ["faq", "product"]),
    ("耳机 SKU 规格", ["product"]),
    ("活动规则的有效期", ["activity"]),
    ("维修申请窗口期", ["policy"]),
    ("售后排除原因", ["policy"]),
    ("规则冲突时如何处理", ["policy"]),
]

SAMPLED_METADATA_FIELDS = (
    "chunk_id",
    "doc_id",
    "doc_type",
    "source_uri",
    "knowledge_version",
    "status",
    "effective_from_epoch",
    "effective_to_epoch",
    "content_hash",
    "product_id",
    "sku_id",
    "category",
)


def sampled_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in SAMPLED_METADATA_FIELDS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a versioned CommerceAgent RAG index")
    add_common_arguments(parser)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = config_from_args(args)
    manifest = load_manifest(config.knowledge_version)
    chunks_path = Path(str(manifest["chunks_path"]))
    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines()]
    expected_count = int(manifest["chunk_count"])
    sample_ids = [item["chunk_id"] for item in chunks[:: max(1, len(chunks) // 100)]][:100]
    expected_hashes = {
        item["chunk_id"]: item["content_hash"]
        for item in chunks
        if item["chunk_id"] in sample_ids
    }
    elasticsearch = ElasticsearchStore(config)
    milvus = MilvusStore(config)
    retriever = HybridRetriever(
        config,
        index_name=physical_name(config.knowledge_version),
        collection_name=physical_name(config.knowledge_version),
    )
    errors: list[str] = []
    try:
        es_count, milvus_count, es_rows, milvus_rows, actual_dimension = await asyncio.gather(
            elasticsearch.count(),
            milvus.count(),
            elasticsearch.sample_rows(sample_ids),
            milvus.sample_rows(sample_ids),
            milvus.dimension(),
        )
        expected_rows = {
            item["chunk_id"]: sampled_metadata(item)
            for item in chunks
            if item["chunk_id"] in sample_ids
        }
        es_metadata = {
            chunk_id: sampled_metadata(row) for chunk_id, row in es_rows.items()
        }
        milvus_metadata = {
            chunk_id: sampled_metadata(row) for chunk_id, row in milvus_rows.items()
        }
        es_hashes = {
            chunk_id: str(row["content_hash"]) for chunk_id, row in es_rows.items()
        }
        milvus_hashes = {
            chunk_id: str(row["content_hash"]) for chunk_id, row in milvus_rows.items()
        }
        if es_count != expected_count:
            errors.append(f"Elasticsearch count: expected {expected_count}, got {es_count}")
        if milvus_count != expected_count:
            errors.append(f"Milvus count: expected {expected_count}, got {milvus_count}")
        if es_hashes != expected_hashes:
            errors.append("Elasticsearch sampled content hashes differ from manifest")
        if milvus_hashes != expected_hashes:
            errors.append("Milvus sampled content hashes differ from manifest")
        if es_metadata != expected_rows:
            errors.append("Elasticsearch sampled metadata differs from manifest")
        if milvus_metadata != expected_rows:
            errors.append("Milvus sampled metadata differs from manifest")
        if actual_dimension != config.embedding.dimension:
            errors.append(
                "Milvus vector dimension: expected "
                f"{config.embedding.dimension}, got {actual_dimension}"
            )

        smoke: list[dict[str, Any]] = []
        for query, doc_types in SMOKE_QUERIES:
            result = await retriever.retrieve(query, doc_types=doc_types)
            valid = result.status == "ok" and bool(result.hits) and validate_citations(result)
            smoke.append(
                {
                    "query": query,
                    "doc_types": doc_types,
                    "passed": valid,
                    "top_chunk_id": result.hits[0].chunk_id if result.hits else None,
                    "decision_score": result.decision_score,
                    "latency_ms": result.stages_ms.get("total"),
                }
            )
        failures = [item for item in smoke if not item["passed"]]
        if failures:
            errors.append(f"{len(failures)} of {len(smoke)} smoke queries failed")
        report = {
            "status": "passed" if not errors else "failed",
            "version": config.knowledge_version,
            "verified_at": datetime.now(UTC).isoformat(),
            "expected_chunks": expected_count,
            "elasticsearch_chunks": es_count,
            "milvus_chunks": milvus_count,
            "sampled_hashes": len(sample_ids),
            "embedding_dimension": actual_dimension,
            "sampled_metadata": len(sample_ids),
            "smoke": smoke,
            "errors": errors,
        }
        report_path = args.report or Path("reports/rag") / f"verify_{config.knowledge_version}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        engine = create_engine()
        factory = session_factory(engine)
        async with factory() as session:
            await update_version_status(
                session,
                config.knowledge_version,
                "verified" if not errors else "failed",
                manifest_updates={"verification_report": report_path.as_posix()},
            )
        await engine.dispose()
        if errors:
            raise RuntimeError("; ".join(errors))
        return report
    finally:
        await retriever.close()
        await elasticsearch.close()


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
