from __future__ import annotations

import argparse

from packages.rag_core.config import RagConfig, load_rag_config


def config_from_args(args: argparse.Namespace) -> RagConfig:
    config = load_rag_config(args.config)
    version = args.version or config.knowledge_version
    return config.model_copy(update={"knowledge_version": version})


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="configs/rag/rag_v1.yaml")
    parser.add_argument("--version")
