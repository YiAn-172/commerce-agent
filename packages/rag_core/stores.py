from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from pymilvus import MilvusClient

from packages.rag_core.config import RagConfig, physical_name
from packages.rag_core.dataset import chunk_payload
from packages.rag_core.models import KnowledgeChunk


def elasticsearch_mapping() -> dict[str, Any]:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "doc_type": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "standard"},
                "text": {"type": "text", "analyzer": "standard"},
                "source_uri": {"type": "keyword"},
                "knowledge_version": {"type": "keyword"},
                "status": {"type": "keyword"},
                "effective_from": {"type": "date"},
                "effective_to": {"type": "date"},
                "effective_from_epoch": {"type": "long"},
                "effective_to_epoch": {"type": "long"},
                "content_hash": {"type": "keyword"},
                "product_id": {"type": "keyword"},
                "sku_id": {"type": "keyword"},
                "category": {"type": "keyword"},
                "metadata": {"type": "object", "dynamic": True},
            },
        },
    }


class ElasticsearchStore:
    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self.client = AsyncElasticsearch(config.stores.elasticsearch_url)

    @property
    def index_name(self) -> str:
        return physical_name(self.config.knowledge_version)

    async def close(self) -> None:
        await self.client.close()

    async def recreate(self) -> None:
        if await self.client.indices.exists(index=self.index_name):
            await self.client.indices.delete(index=self.index_name)
        await self.client.indices.create(index=self.index_name, **elasticsearch_mapping())

    async def index_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        actions = (
            {
                "_index": self.index_name,
                "_id": chunk.chunk_id,
                "_source": chunk_payload(chunk),
            }
            for chunk in chunks
        )
        success, errors = await async_bulk(
            self.client,
            actions,
            chunk_size=250,
            raise_on_error=False,
        )
        error_count = len(errors) if isinstance(errors, list) else int(errors)
        if error_count or success != len(chunks):
            raise RuntimeError(
                f"Elasticsearch bulk mismatch: success={success}, errors={error_count}"
            )
        await self.client.indices.refresh(index=self.index_name)

    async def count(self, index: str | None = None) -> int:
        result = await self.client.count(index=index or self.index_name)
        return int(result["count"])

    async def sample_rows(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        result = await self.client.mget(index=self.index_name, ids=chunk_ids)
        return {
            str(document["_id"]): dict(document["_source"])
            for document in result["docs"]
            if document.get("found")
        }

    async def hashes(self, chunk_ids: list[str]) -> dict[str, str]:
        rows = await self.sample_rows(chunk_ids)
        return {chunk_id: str(row["content_hash"]) for chunk_id, row in rows.items()}

    async def alias_targets(self) -> list[str]:
        try:
            result = await self.client.indices.get_alias(
                name=self.config.stores.elasticsearch_alias
            )
        except Exception:
            return []
        return sorted(str(name) for name in result)

    async def activate(self) -> list[str]:
        previous = await self.alias_targets()
        actions: list[dict[str, Any]] = [
            {"remove": {"index": name, "alias": self.config.stores.elasticsearch_alias}}
            for name in previous
        ]
        actions.append(
            {
                "add": {
                    "index": self.index_name,
                    "alias": self.config.stores.elasticsearch_alias,
                }
            }
        )
        await self.client.indices.update_aliases(actions=actions)
        return previous

    async def restore_alias(self, targets: list[str]) -> None:
        current = await self.alias_targets()
        actions: list[dict[str, Any]] = [
            {"remove": {"index": name, "alias": self.config.stores.elasticsearch_alias}}
            for name in current
        ]
        actions.extend(
            {"add": {"index": name, "alias": self.config.stores.elasticsearch_alias}}
            for name in targets
        )
        if actions:
            await self.client.indices.update_aliases(actions=actions)


class MilvusStore:
    def __init__(self, config: RagConfig) -> None:
        self.config = config
        self.client = MilvusClient(uri=config.stores.milvus_uri)

    @property
    def collection_name(self) -> str:
        return physical_name(self.config.knowledge_version)

    async def recreate(self) -> None:
        def operation() -> None:
            if self.client.has_collection(self.collection_name):
                self.client.drop_collection(self.collection_name)
            self.client.create_collection(
                collection_name=self.collection_name,
                dimension=self.config.embedding.dimension,
                metric_type="IP",
                consistency_level="Strong",
                auto_id=False,
                primary_field_name="chunk_id",
                vector_field_name="vector",
                id_type="string",
                max_length=160,
                enable_dynamic_field=True,
            )

        await asyncio.to_thread(operation)

    async def insert(self, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk/vector cardinality mismatch")

        def batches() -> Iterable[list[dict[str, Any]]]:
            rows = []
            for chunk, vector in zip(chunks, vectors, strict=True):
                row = chunk_payload(chunk)
                row["vector"] = vector
                rows.append(row)
                if len(rows) == 200:
                    yield rows
                    rows = []
            if rows:
                yield rows

        def operation() -> None:
            for batch in batches():
                result = self.client.insert(self.collection_name, data=batch)
                if int(result["insert_count"]) != len(batch):
                    raise RuntimeError("Milvus insert cardinality mismatch")
            self.client.flush(self.collection_name)
            self.client.load_collection(self.collection_name)

        await asyncio.to_thread(operation)

    async def count(self) -> int:
        def operation() -> int:
            result = self.client.get_collection_stats(self.collection_name)
            return int(result["row_count"])

        return await asyncio.to_thread(operation)

    async def sample_rows(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        def operation() -> dict[str, dict[str, Any]]:
            quoted = ",".join(json_quote(value) for value in chunk_ids)
            rows = self.client.query(
                self.collection_name,
                filter=f"chunk_id in [{quoted}]",
                output_fields=["*"],
                limit=len(chunk_ids),
            )
            return {str(row["chunk_id"]): dict(row) for row in rows}

        return await asyncio.to_thread(operation)

    async def hashes(self, chunk_ids: list[str]) -> dict[str, str]:
        rows = await self.sample_rows(chunk_ids)
        return {chunk_id: str(row["content_hash"]) for chunk_id, row in rows.items()}

    async def dimension(self) -> int:
        def operation() -> int:
            description = self.client.describe_collection(self.collection_name)
            fields = description.get("fields", [])
            for field in fields:
                if field.get("name") == "vector":
                    params = field.get("params", {})
                    return int(params["dim"])
            raise RuntimeError("Milvus vector field is missing from collection schema")

        return await asyncio.to_thread(operation)

    def _alias_target_sync(self) -> str | None:
        alias = self.config.stores.milvus_alias
        for collection in self.client.list_collections():
            result = self.client.list_aliases(collection_name=collection)
            aliases = result.get("aliases", []) if isinstance(result, dict) else result
            if alias in aliases:
                return str(collection)
        return None

    async def alias_target(self) -> str | None:
        return await asyncio.to_thread(self._alias_target_sync)

    async def activate(self) -> str | None:
        previous = await self.alias_target()

        def operation() -> None:
            if previous is None:
                self.client.create_alias(
                    collection_name=self.collection_name,
                    alias=self.config.stores.milvus_alias,
                )
            else:
                self.client.alter_alias(
                    collection_name=self.collection_name,
                    alias=self.config.stores.milvus_alias,
                )

        await asyncio.to_thread(operation)
        return previous

    async def restore_alias(self, target: str | None) -> None:
        if target is None:
            current = await self.alias_target()
            if current is not None:
                await asyncio.to_thread(
                    self.client.drop_alias,
                    alias=self.config.stores.milvus_alias,
                )
            return
        await asyncio.to_thread(
            self.client.alter_alias,
            collection_name=target,
            alias=self.config.stores.milvus_alias,
        )


def json_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'
