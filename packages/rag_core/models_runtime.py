from __future__ import annotations

from typing import Any, cast

import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer

from packages.rag_core.config import EmbeddingConfig, RerankerConfig


class BgeEmbedder:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(
                self.config.model_id,
                revision=self.config.revision,
                device="cpu",
                cache_folder=None,
            )
            dimension = self._model.get_embedding_dimension()
            if dimension != self.config.dimension:
                raise RuntimeError(
                    "embedding dimension mismatch: "
                    f"expected {self.config.dimension}, got {dimension}"
                )
        return self._model

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts,
            batch_size=self.config.batch_size,
            normalize_embeddings=self.config.normalize,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        return cast(list[list[float]], np.asarray(vectors, dtype=np.float32).tolist())

    def encode_query(self, query: str) -> list[float]:
        value = f"{self.config.query_instruction}{query}"
        vector = self.model.encode(
            [value],
            normalize_embeddings=self.config.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )[0]
        return cast(list[float], np.asarray(vector, dtype=np.float32).tolist())


class BgeReranker:
    def __init__(self, config: RerankerConfig) -> None:
        self.config = config
        self._model: CrossEncoder | None = None

    @property
    def model(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(
                self.config.model_id,
                revision=self.config.revision,
                device="cpu",
                max_length=512,
            )
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs: list[list[str]] = [[query, passage] for passage in passages]
        scores: Any = self.model.predict(
            pairs,  # type: ignore[arg-type]
            batch_size=self.config.batch_size,
            show_progress_bar=False,
        )
        return [float(value) for value in np.asarray(scores).reshape(-1)]
