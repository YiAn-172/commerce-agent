from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field

from packages.contracts.base import StrictModel


class EmbeddingConfig(StrictModel):
    model_id: str
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    dimension: int = Field(gt=0)
    normalize: bool = True
    query_instruction: str
    batch_size: int = Field(ge=1, le=256)


class RerankerConfig(StrictModel):
    model_id: str
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    batch_size: int = Field(ge=1, le=128)


class RetrievalConfig(StrictModel):
    bm25_top_k: int = Field(ge=1, le=100)
    vector_top_k: int = Field(ge=1, le=100)
    rrf_k: int = Field(ge=1, le=1000)
    fusion_top_k: int = Field(ge=1, le=100)
    rerank_top_k: int = Field(ge=1, le=20)
    insufficient_score: float


class StoreConfig(StrictModel):
    elasticsearch_url: str
    elasticsearch_alias: str
    milvus_uri: str
    milvus_alias: str


class EvaluationConfig(StrictModel):
    suite: str
    product_cases: int
    policy_cases: int
    activity_cases: int
    no_answer_cases: int


class RagConfig(StrictModel):
    schema_version: str
    knowledge_version: str
    embedding: EmbeddingConfig
    reranker: RerankerConfig
    retrieval: RetrievalConfig
    stores: StoreConfig
    evaluation: EvaluationConfig


@lru_cache(maxsize=4)
def load_rag_config(path: str = "configs/rag/rag_v1.yaml") -> RagConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RagConfig.model_validate(payload)


def physical_name(version: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in version)
    return f"commerce_kb_{normalized.lower()}"
