from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select

from packages.business.database import create_engine, session_factory
from packages.business.models import KnowledgeVersion
from packages.rag_core.ingest.common import add_common_arguments, config_from_args
from packages.rag_core.stores import ElasticsearchStore, MilvusStore


class ElasticsearchAliasStore(Protocol):
    async def activate(self) -> list[str]: ...

    async def restore_alias(self, targets: list[str]) -> None: ...


class MilvusAliasStore(Protocol):
    async def activate(self) -> str | None: ...

    async def restore_alias(self, target: str | None) -> None: ...


async def switch_aliases(
    elasticsearch: ElasticsearchAliasStore,
    milvus: MilvusAliasStore,
    persist: Callable[[], Awaitable[None]],
) -> tuple[list[str], str | None]:
    previous_es = await elasticsearch.activate()
    try:
        previous_milvus = await milvus.activate()
    except Exception:
        await elasticsearch.restore_alias(previous_es)
        raise
    try:
        await persist()
    except Exception:
        await asyncio.gather(
            elasticsearch.restore_alias(previous_es),
            milvus.restore_alias(previous_milvus),
        )
        raise
    return previous_es, previous_milvus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atomically activate a verified RAG version")
    add_common_arguments(parser)
    return parser.parse_args()


async def activate_version(args: argparse.Namespace) -> dict[str, Any]:
    config = config_from_args(args)
    engine = create_engine()
    factory = session_factory(engine)
    elasticsearch = ElasticsearchStore(config)
    milvus = MilvusStore(config)
    previous_es: list[str] = []
    previous_milvus: str | None = None
    try:
        async with factory() as session:
            target = await session.scalar(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.version == config.knowledge_version
                )
            )
            if target is None or target.status not in {"verified", "superseded", "active"}:
                status = None if target is None else target.status
                raise RuntimeError(f"version must be verified before activation; status={status}")
        now = datetime.now(UTC)

        async def persist() -> None:
            async with factory() as session:
                active_versions = list(
                    (
                        await session.scalars(
                            select(KnowledgeVersion).where(KnowledgeVersion.status == "active")
                        )
                    ).all()
                )
                for active in active_versions:
                    active.status = "superseded"
                target = await session.scalar(
                    select(KnowledgeVersion).where(
                        KnowledgeVersion.version == config.knowledge_version
                    )
                )
                if target is None:
                    raise RuntimeError("target version disappeared during activation")
                target.status = "active"
                target.activated_at = now
                await session.commit()

        previous_es, previous_milvus = await switch_aliases(
            elasticsearch, milvus, persist
        )
        return {
            "status": "active",
            "version": config.knowledge_version,
            "activated_at": now.isoformat(),
            "previous_elasticsearch_targets": previous_es,
            "previous_milvus_target": previous_milvus,
        }
    finally:
        await elasticsearch.close()
        await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(activate_version(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
