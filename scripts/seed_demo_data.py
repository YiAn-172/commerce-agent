from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business.database import create_engine, session_factory
from packages.business.demo_data import DATA_VERSION, build_demo_dataset
from packages.business.models import Base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed deterministic CommerceAgent demo data")
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/manifests/demo_business_v1.json")
    )
    return parser.parse_args()


async def table_count(session: AsyncSession, model: type[Any]) -> int:
    value = await session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


async def insert_chunks(
    session: AsyncSession,
    model: type[Any],
    records: list[dict[str, Any]],
    chunk_size: int = 1000,
) -> None:
    for start in range(0, len(records), chunk_size):
        await session.execute(insert(model), records[start : start + chunk_size])


async def seed(args: argparse.Namespace) -> dict[str, Any]:
    dataset = build_demo_dataset(args.seed)
    engine = create_engine()
    factory = session_factory(engine)
    insertion_order = list(dataset.rows)
    async with factory() as session:
        existing = {
            model.__tablename__: await table_count(session, model) for model in insertion_order
        }
        if any(existing.values()) and not args.reset:
            expected = {
                model.__tablename__: len(records) for model, records in dataset.rows.items()
            }
            exact = all(
                existing.get(name) == expected_count
                if name != "knowledge_versions"
                else int(existing.get(name, 0)) >= expected_count
                for name, expected_count in expected.items()
            )
            if not exact:
                raise RuntimeError(
                    "Demo tables contain partial or modified data; use --reset explicitly "
                    f"after reviewing counts: {existing}"
                )
            await engine.dispose()
            return {
                "status": "already_seeded",
                "seed": args.seed,
                "data_version": DATA_VERSION,
                "counts": existing,
                "scenario_ids": dataset.scenario_ids,
                "dataset_fingerprint": dataset.fingerprint,
            }
        await session.rollback()
        async with session.begin():
            if args.reset:
                for table in reversed(list(Base.metadata.sorted_tables)):
                    await session.execute(delete(table))
            for model, records in dataset.rows.items():
                await insert_chunks(session, model, records)
    await engine.dispose()
    counts = {model.__tablename__: len(records) for model, records in dataset.rows.items()}
    return {
        "status": "seeded",
        "seed": args.seed,
        "data_version": DATA_VERSION,
        "counts": counts,
        "scenario_ids": dataset.scenario_ids,
        "dataset_fingerprint": dataset.fingerprint,
    }


def main() -> None:
    args = parse_args()
    result = asyncio.run(seed(args))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
