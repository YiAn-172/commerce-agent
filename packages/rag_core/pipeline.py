from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business.models import KnowledgeDocument, KnowledgeVersion
from packages.rag_core.dataset import KnowledgeDataset, chunk_payload


def manifest_path(version: str) -> Path:
    return Path("data/manifests") / f"{version}.json"


def export_dir(version: str) -> Path:
    return Path("data/processed/knowledge") / version


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_dataset(dataset: KnowledgeDataset, version: str) -> dict[str, Any]:
    target = export_dir(version)
    target.mkdir(parents=True, exist_ok=True)
    documents_path = target / "documents.jsonl"
    chunks_path = target / "chunks.jsonl"
    documents_path.write_text(
        "\n".join(canonical_json(item.model_dump(mode="json")) for item in dataset.documents)
        + "\n",
        encoding="utf-8",
    )
    chunks_path.write_text(
        "\n".join(canonical_json(chunk_payload(item)) for item in dataset.chunks) + "\n",
        encoding="utf-8",
    )
    by_type: dict[str, int] = {}
    for document in dataset.documents:
        by_type[document.doc_type] = by_type.get(document.doc_type, 0) + 1
    manifest = {
        "schema_version": "1.0",
        "knowledge_version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "document_count": len(dataset.documents),
        "chunk_count": len(dataset.chunks),
        "document_counts_by_type": dict(sorted(by_type.items())),
        "dataset_fingerprint": dataset.fingerprint,
        "documents_sha256": sha256_file(documents_path),
        "chunks_sha256": sha256_file(chunks_path),
        "documents_path": documents_path.as_posix(),
        "chunks_path": chunks_path.as_posix(),
    }
    path = manifest_path(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_manifest(version: str) -> dict[str, Any]:
    path = manifest_path(version)
    if not path.exists():
        raise RuntimeError(f"knowledge manifest is missing: {path}")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


async def register_build(
    session: AsyncSession,
    dataset: KnowledgeDataset,
    version: str,
    manifest: dict[str, Any],
) -> None:
    row = await session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.version == version))
    now = datetime.now(UTC)
    if row is None:
        row = KnowledgeVersion(
            id=f"kbv_{version}"[:48],
            version=version,
            status="building",
            manifest_json=manifest,
            activated_at=None,
            created_at=now,
        )
        session.add(row)
    else:
        row.status = "building"
        row.manifest_json = manifest
        row.activated_at = None

    await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.like("rag_%")))
    for document in dataset.documents:
        if "api_document_id" in document.metadata:
            continue
        session.add(
            KnowledgeDocument(
                id=f"rag_{document.doc_id}"[:48],
                document_type=document.doc_type,
                title=document.title,
                source_uri=document.source_uri,
                content_sha256=hashlib.sha256(document.content.encode()).hexdigest(),
                content_text=document.content,
                created_by="rag_build_pipeline",
                status=document.status,
            )
        )
    await session.commit()


async def update_version_status(
    session: AsyncSession,
    version: str,
    status: str,
    *,
    manifest_updates: dict[str, Any] | None = None,
) -> None:
    row = await session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.version == version))
    if row is None:
        raise RuntimeError(f"knowledge version is not registered: {version}")
    row.status = status
    if manifest_updates:
        row.manifest_json = {**row.manifest_json, **manifest_updates}
    await session.commit()
