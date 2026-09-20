from __future__ import annotations

from packages.rag_core.config import load_rag_config, physical_name


def test_rag_config_pins_models_and_dimensions() -> None:
    config = load_rag_config()
    assert config.embedding.model_id == "BAAI/bge-small-zh-v1.5"
    assert len(config.embedding.revision) == 40
    assert config.embedding.dimension == 512
    assert config.reranker.model_id == "BAAI/bge-reranker-base"
    assert len(config.reranker.revision) == 40


def test_physical_name_is_stable_and_safe() -> None:
    assert physical_name("KB-2026.09/v1") == "commerce_kb_kb_2026_09_v1"
