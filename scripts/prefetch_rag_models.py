from __future__ import annotations

import argparse
import json

from huggingface_hub import snapshot_download

from packages.rag_core.config import load_rag_config

REQUIRED_MODEL_FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "sentencepiece.bpe.model",
    "modules.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
    "1_Pooling/config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Prefetch pinned RAG models")
    parser.add_argument("--config", default="configs/rag/rag_v1.yaml")
    args = parser.parse_args()
    config = load_rag_config(args.config)
    resolved = {}
    for name, model_id, revision in (
        ("embedding", config.embedding.model_id, config.embedding.revision),
        ("reranker", config.reranker.model_id, config.reranker.revision),
    ):
        resolved[name] = snapshot_download(
            repo_id=model_id,
            revision=revision,
            allow_patterns=REQUIRED_MODEL_FILES,
        )
    print(json.dumps(resolved, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
