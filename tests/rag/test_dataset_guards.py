from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from packages.rag_core.dataset import chunk_payload
from packages.rag_core.models import KnowledgeChunk


def test_chunk_payload_has_required_versioning_and_provenance_fields() -> None:
    text = "稳定产品参数，不含任何用户交易事实。"
    chunk = KnowledgeChunk(
        chunk_id="chk_001",
        doc_id="doc_001",
        doc_type="product",
        title="演示产品",
        text=text,
        source_uri="demo://product/001",
        knowledge_version="kb_001",
        status="active",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    payload = chunk_payload(chunk)
    required = {
        "chunk_id",
        "doc_id",
        "doc_type",
        "knowledge_version",
        "effective_from",
        "effective_to",
        "content_hash",
        "source_uri",
    }
    assert required <= payload.keys()
    assert payload["effective_to_epoch"] == 253402300799
