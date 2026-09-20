from __future__ import annotations

import pytest

from packages.rag_core.ingest.activate import switch_aliases
from packages.rag_core.stores import MilvusStore


class FakeElasticsearch:
    def __init__(self) -> None:
        self.restored: list[str] | None = None

    async def activate(self) -> list[str]:
        return ["old_es"]

    async def restore_alias(self, targets: list[str]) -> None:
        self.restored = targets


class FakeMilvus:
    def __init__(self) -> None:
        self.restored: str | None = "not-called"

    async def activate(self) -> str | None:
        return None

    async def restore_alias(self, target: str | None) -> None:
        self.restored = target


@pytest.mark.asyncio
async def test_database_failure_restores_both_aliases_including_first_activation() -> None:
    elasticsearch = FakeElasticsearch()
    milvus = FakeMilvus()

    async def fail_persistence() -> None:
        raise RuntimeError("database commit failed")

    with pytest.raises(RuntimeError, match="database commit failed"):
        await switch_aliases(elasticsearch, milvus, fail_persistence)

    assert elasticsearch.restored == ["old_es"]
    assert milvus.restored is None


def test_milvus_alias_lookup_supports_mapping_response() -> None:
    class Client:
        def list_collections(self) -> list[str]:
            return ["old_collection", "active_collection"]

        def list_aliases(self, *, collection_name: str) -> dict[str, list[str]]:
            return {
                "aliases": ["commerce_kb_active"]
                if collection_name == "active_collection"
                else []
            }

    store = object.__new__(MilvusStore)
    store.client = Client()
    store.config = type(
        "Config",
        (),
        {"stores": type("Stores", (), {"milvus_alias": "commerce_kb_active"})()},
    )()
    assert store._alias_target_sync() == "active_collection"
