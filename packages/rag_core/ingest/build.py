from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime

from packages.business.database import create_engine, session_factory
from packages.rag_core.dataset import build_knowledge_dataset
from packages.rag_core.ingest.common import add_common_arguments, config_from_args
from packages.rag_core.models_runtime import BgeEmbedder
from packages.rag_core.pipeline import export_dataset, register_build, update_version_status
from packages.rag_core.stores import ElasticsearchStore, MilvusStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a versioned CommerceAgent RAG index")
    add_common_arguments(parser)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, object]:
    config = config_from_args(args)
    engine = create_engine()
    factory = session_factory(engine)
    elasticsearch = ElasticsearchStore(config)
    milvus = MilvusStore(config)
    try:
        async with factory() as session:
            dataset = await build_knowledge_dataset(session, config.knowledge_version)
            manifest = export_dataset(dataset, config.knowledge_version)
            manifest.update(
                {
                    "embedding_model": config.embedding.model_id,
                    "embedding_revision": config.embedding.revision,
                    "embedding_dimension": config.embedding.dimension,
                    "build_started_at": datetime.now(UTC).isoformat(),
                }
            )
            await register_build(
                session, dataset, config.knowledge_version, manifest
            )

        await asyncio.gather(elasticsearch.recreate(), milvus.recreate())
        vectors = await asyncio.to_thread(
            BgeEmbedder(config.embedding).encode_documents,
            [chunk.text for chunk in dataset.chunks],
        )
        await asyncio.gather(
            elasticsearch.index_chunks(dataset.chunks),
            milvus.insert(dataset.chunks, vectors),
        )
        completed = datetime.now(UTC).isoformat()
        manifest["build_completed_at"] = completed
        async with factory() as session:
            await update_version_status(
                session,
                config.knowledge_version,
                "indexed",
                manifest_updates={"build_completed_at": completed},
            )
        return {
            "status": "indexed",
            "version": config.knowledge_version,
            "documents": len(dataset.documents),
            "chunks": len(dataset.chunks),
            "fingerprint": dataset.fingerprint,
            "elasticsearch_index": elasticsearch.index_name,
            "milvus_collection": milvus.collection_name,
        }
    except Exception:
        async with factory() as session:
            # Status persistence is best-effort: never hide the original build failure
            # when the database itself is unavailable.
            with suppress(Exception):
                await update_version_status(session, config.knowledge_version, "failed")
        raise
    finally:
        await elasticsearch.close()
        await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
