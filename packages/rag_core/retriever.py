from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from elasticsearch import AsyncElasticsearch
from pymilvus import MilvusClient

from packages.rag_core.config import RagConfig
from packages.rag_core.models import Citation, RetrievalHit, RetrievalResult
from packages.rag_core.models_runtime import BgeEmbedder, BgeReranker

RetrievalVariant = Literal["bm25", "vector", "hybrid", "hybrid_rerank"]


def reciprocal_rank_fusion(
    bm25: list[RetrievalHit], vector: list[RetrievalHit], *, rrf_k: int
) -> list[RetrievalHit]:
    merged: dict[str, RetrievalHit] = {}
    scores: dict[str, float] = {}
    for source, field in ((bm25, "bm25_rank"), (vector, "vector_rank")):
        for rank, hit in enumerate(source, start=1):
            update = {field: rank, "score": 0.0}
            if hit.chunk_id not in merged:
                merged[hit.chunk_id] = hit.model_copy(update=update)
            else:
                merged[hit.chunk_id] = merged[hit.chunk_id].model_copy(update=update)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(
        (hit.model_copy(update={"score": scores[key]}) for key, hit in merged.items()),
        key=lambda item: (-item.score, item.chunk_id),
    )


def _active_filters(
    now: datetime,
    *,
    doc_types: list[str] | None = None,
    candidate_product_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [
        {"term": {"status": "active"}},
        {"range": {"effective_from": {"lte": now.isoformat()}}},
        {
            "bool": {
                "should": [
                    {"bool": {"must_not": {"exists": {"field": "effective_to"}}}},
                    {"range": {"effective_to": {"gte": now.isoformat()}}},
                ],
                "minimum_should_match": 1,
            }
        },
    ]
    if doc_types:
        values.append({"terms": {"doc_type": doc_types}})
    if candidate_product_ids is not None:
        values.append(
            {
                "bool": {
                    "should": [
                        {"bool": {"must_not": {"term": {"doc_type": "product"}}}},
                        {"terms": {"product_id": sorted(candidate_product_ids)}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return values


def _milvus_filter(
    now: datetime,
    doc_types: list[str] | None,
    candidate_product_ids: set[str] | None = None,
) -> str:
    parts = [
        'status == "active"',
        f"effective_from_epoch <= {int(now.timestamp())}",
        f"effective_to_epoch >= {int(now.timestamp())}",
    ]
    if doc_types:
        quoted = ",".join(f'"{value}"' for value in doc_types)
        parts.append(f"doc_type in [{quoted}]")
    if candidate_product_ids is not None:
        candidates = sorted(candidate_product_ids) or ["__no_candidate__"]
        quoted = ",".join(f'"{value}"' for value in candidates)
        parts.append(f'(doc_type != "product" or product_id in [{quoted}])')
    return " and ".join(parts)


def _hit(payload: dict[str, Any], score: float) -> RetrievalHit:
    normalized = dict(payload)
    for field in ("effective_from", "effective_to"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return RetrievalHit.model_validate(
        {
            **{
                key: normalized.get(key)
                for key in (
                    "chunk_id",
                    "doc_id",
                    "doc_type",
                    "title",
                    "text",
                    "source_uri",
                    "knowledge_version",
                    "content_hash",
                    "effective_from",
                    "effective_to",
                    "product_id",
                    "sku_id",
                    "category",
                )
            },
            "score": score,
        }
    )


def should_reject(
    hits: list[RetrievalHit],
    *,
    variant: RetrievalVariant,
    doc_types: list[str] | None,
    threshold: float,
) -> bool:
    if not hits:
        return True
    # A routed intent already constrains retrieval to an approved knowledge domain.
    # The score gate protects only the untyped fallback, where an unrelated query
    # can otherwise be matched to the nearest available commerce document.
    return (
        variant == "hybrid_rerank"
        and doc_types is None
        and hits[0].score < threshold
    )


class HybridRetriever:
    def __init__(
        self,
        config: RagConfig,
        *,
        index_name: str | None = None,
        collection_name: str | None = None,
        embedder: BgeEmbedder | None = None,
        reranker: BgeReranker | None = None,
    ) -> None:
        self.config = config
        self.index_name = index_name or config.stores.elasticsearch_alias
        self.collection_name = collection_name or config.stores.milvus_alias
        self.elasticsearch = AsyncElasticsearch(config.stores.elasticsearch_url)
        self.milvus = MilvusClient(uri=config.stores.milvus_uri)
        self.embedder = embedder or BgeEmbedder(config.embedding)
        self.reranker = reranker or BgeReranker(config.reranker)

    async def close(self) -> None:
        await self.elasticsearch.close()

    async def bm25(
        self,
        query: str,
        doc_types: list[str] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> list[RetrievalHit]:
        response = await self.elasticsearch.search(
            index=self.index_name,
            size=self.config.retrieval.bm25_top_k,
            query={
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^3", "text"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": _active_filters(
                        datetime.now(UTC),
                        doc_types=doc_types,
                        candidate_product_ids=candidate_product_ids,
                    ),
                }
            },
        )
        return [_hit(item["_source"], float(item["_score"])) for item in response["hits"]["hits"]]

    async def vector(
        self,
        query: str,
        doc_types: list[str] | None = None,
        *,
        vector: list[float] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> list[RetrievalHit]:
        query_vector = vector or await asyncio.to_thread(self.embedder.encode_query, query)

        def operation() -> list[RetrievalHit]:
            result = self.milvus.search(
                collection_name=self.collection_name,
                data=[query_vector],
                anns_field="vector",
                filter=_milvus_filter(
                    datetime.now(UTC), doc_types, candidate_product_ids
                ),
                limit=self.config.retrieval.vector_top_k,
                output_fields=["*"],
            )[0]
            return [_hit(item["entity"], float(item["distance"])) for item in result]

        return await asyncio.to_thread(operation)

    async def retrieve(
        self,
        query: str,
        *,
        variant: RetrievalVariant = "hybrid_rerank",
        doc_types: list[str] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        stages: dict[str, int] = {}
        if variant == "bm25":
            hits = await self.bm25(query, doc_types, candidate_product_ids)
        elif variant == "vector":
            hits = await self.vector(
                query, doc_types, candidate_product_ids=candidate_product_ids
            )
        else:
            parallel_started = time.perf_counter()
            bm25_hits, vector_hits = await asyncio.gather(
                self.bm25(query, doc_types, candidate_product_ids),
                self.vector(
                    query, doc_types, candidate_product_ids=candidate_product_ids
                ),
            )
            stages["parallel_retrieval"] = int((time.perf_counter() - parallel_started) * 1000)
            hits = reciprocal_rank_fusion(
                bm25_hits, vector_hits, rrf_k=self.config.retrieval.rrf_k
            )[: self.config.retrieval.fusion_top_k]
        if candidate_product_ids is not None:
            hits = [
                hit
                for hit in hits
                if hit.doc_type != "product" or hit.product_id in candidate_product_ids
            ]
        reranker_name: str | None = None
        if variant == "hybrid_rerank" and hits:
            rerank_started = time.perf_counter()
            rerank_scores = await asyncio.to_thread(
                self.reranker.score, query, [item.text for item in hits]
            )
            hits = sorted(
                (
                    hit.model_copy(update={"score": score, "rerank_score": score})
                    for hit, score in zip(hits, rerank_scores, strict=True)
                ),
                key=lambda item: (-item.score, item.chunk_id),
            )
            stages["rerank"] = int((time.perf_counter() - rerank_started) * 1000)
            reranker_name = self.config.reranker.model_id
        hits = hits[: self.config.retrieval.rerank_top_k]
        decision_score = hits[0].score if hits else None
        insufficient = should_reject(
            hits,
            variant=variant,
            doc_types=doc_types,
            threshold=self.config.retrieval.insufficient_score,
        )
        citations = [] if insufficient else [citation_from_hit(hit) for hit in hits]
        stages["total"] = int((time.perf_counter() - started) * 1000)
        return RetrievalResult(
            query=query,
            status="insufficient_evidence" if insufficient else "ok",
            hits=[] if insufficient else hits,
            citations=citations,
            stages_ms=stages,
            embedding_model=self.config.embedding.model_id,
            reranker_model=reranker_name,
            decision_score=decision_score,
        )


def citation_from_hit(hit: RetrievalHit) -> Citation:
    return Citation(
        chunk_id=hit.chunk_id,
        doc_id=hit.doc_id,
        title=hit.title,
        source_uri=hit.source_uri,
        knowledge_version=hit.knowledge_version,
        content_hash=hit.content_hash,
        effective_from=hit.effective_from,
    )


def validate_citations(result: RetrievalResult) -> bool:
    hits = {item.chunk_id: item for item in result.hits}
    return all(
        citation.chunk_id in hits
        and citation.content_hash == hits[citation.chunk_id].content_hash
        and citation.knowledge_version == hits[citation.chunk_id].knowledge_version
        for citation in result.citations
    )


def render_untrusted_evidence(hits: Iterable[RetrievalHit]) -> str:
    blocks = []
    for hit in hits:
        safe_text = hit.text.replace("</retrieved_document>", "&lt;/retrieved_document&gt;")
        blocks.append(
            f'<retrieved_document chunk_id="{hit.chunk_id}" trust="untrusted">\n'
            f"{safe_text}\n</retrieved_document>"
        )
    return "\n".join(blocks)
