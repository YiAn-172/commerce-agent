from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from packages.business.database import create_engine, session_factory
from packages.business.models import KnowledgeDocument, KnowledgeReindexJob
from packages.rag_core.ingest.activate import activate_version
from packages.rag_core.ingest.build import run as build_index
from packages.rag_core.ingest.verify import run as verify_index


async def claim_job() -> tuple[str, str, list[str], dict[str, str]] | None:
    engine = create_engine()
    factory = session_factory(engine)
    try:
        async with factory() as session, session.begin():
            job = await session.scalar(
                select(KnowledgeReindexJob)
                .where(KnowledgeReindexJob.status == "queued")
                .order_by(KnowledgeReindexJob.created_at)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            query = select(KnowledgeDocument).where(KnowledgeDocument.id.like("kdoc_%"))
            if job.document_ids:
                query = query.where(KnowledgeDocument.id.in_(job.document_ids))
            documents = list((await session.scalars(query)).all())
            previous = {item.id: item.status for item in documents}
            for document in documents:
                document.status = "active"
            job.status = "running"
            job.started_at = datetime.now(UTC).replace(tzinfo=None)
            return job.id, job.target_version, [item.id for item in documents], previous
    finally:
        await engine.dispose()


async def finish_job(
    job_id: str,
    *,
    status: str,
    error_summary: str | None,
    restore_statuses: dict[str, str] | None = None,
) -> None:
    engine = create_engine()
    factory = session_factory(engine)
    try:
        async with factory() as session, session.begin():
            job = await session.get(KnowledgeReindexJob, job_id)
            if job is None:
                raise RuntimeError(f"knowledge reindex job disappeared: {job_id}")
            if restore_statuses:
                documents = list(
                    (
                        await session.scalars(
                            select(KnowledgeDocument).where(
                                KnowledgeDocument.id.in_(restore_statuses)
                            )
                        )
                    ).all()
                )
                for document in documents:
                    document.status = restore_statuses[document.id]
            job.status = status
            job.error_summary = error_summary
            job.completed_at = datetime.now(UTC).replace(tzinfo=None)
    finally:
        await engine.dispose()


async def main_async() -> None:
    claimed = await claim_job()
    if claimed is None:
        print(json.dumps({"status": "idle", "reason": "no queued jobs"}))
        return
    job_id, version, document_ids, previous = claimed
    args = argparse.Namespace(
        config="configs/rag/rag_v1.yaml",
        version=version,
        report=Path("reports/rag") / f"verify_{version}.json",
    )
    try:
        build_result = await build_index(args)
        verify_result = await verify_index(args)
        activate_result = await activate_version(args)
        await finish_job(job_id, status="completed", error_summary=None)
        report: dict[str, Any] = {
            "status": "completed",
            "job_id": job_id,
            "version": version,
            "document_ids": document_ids,
            "build": build_result,
            "verify": verify_result,
            "activate": activate_result,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as error:
        summary = f"{type(error).__name__}: {error}"[:500]
        await finish_job(
            job_id,
            status="failed",
            error_summary=summary,
            restore_statuses=previous,
        )
        raise


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
