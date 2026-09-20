from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from packages.contracts.base import StrictModel


class KnowledgeSourceDocument(StrictModel):
    doc_id: str
    doc_type: Literal["faq", "policy", "activity", "product"]
    title: str
    content: str
    source_uri: str
    version: str
    status: Literal["active", "draft", "expired"] = "active"
    effective_from: datetime
    effective_to: datetime | None = None
    product_id: str | None = None
    sku_id: str | None = None
    category: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunk(StrictModel):
    chunk_id: str
    doc_id: str
    doc_type: Literal["faq", "policy", "activity", "product"]
    title: str
    text: str
    source_uri: str
    knowledge_version: str
    status: Literal["active", "draft", "expired"]
    effective_from: datetime
    effective_to: datetime | None = None
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    product_id: str | None = None
    sku_id: str | None = None
    category: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalHit(StrictModel):
    chunk_id: str
    doc_id: str
    doc_type: str
    title: str
    text: str
    source_uri: str
    knowledge_version: str
    content_hash: str
    effective_from: datetime
    effective_to: datetime | None = None
    product_id: str | None = None
    sku_id: str | None = None
    category: str | None = None
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    rerank_score: float | None = None


class Citation(StrictModel):
    chunk_id: str
    doc_id: str
    title: str
    source_uri: str
    knowledge_version: str
    content_hash: str
    effective_from: datetime


class RetrievalResult(StrictModel):
    query: str
    status: Literal["ok", "insufficient_evidence", "knowledge_conflict"]
    hits: list[RetrievalHit]
    citations: list[Citation]
    stages_ms: dict[str, int]
    embedding_model: str
    reranker_model: str | None = None
    decision_score: float | None = None
