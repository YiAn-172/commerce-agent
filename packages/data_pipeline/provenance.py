from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import yaml

from packages.data_pipeline.schemas import DataSourceRecord


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_directory(path: Path) -> tuple[str, int, int]:
    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and ".git" not in item.relative_to(path).parts
    )
    if not files:
        raise ValueError(f"No files found under {path}")

    digest = hashlib.sha256()
    total_bytes = 0
    for item in files:
        relative = item.relative_to(path).as_posix()
        file_hash = sha256_file(item)
        size = item.stat().st_size
        total_bytes += size
        digest.update(f"{relative}\0{size}\0{file_hash}\n".encode())
    return digest.hexdigest(), len(files), total_bytes


def load_source_config(path: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), dict):
        raise ValueError(f"Invalid source configuration: {path}")
    return cast(dict[str, dict[str, Any]], raw["sources"])


def load_manifest(path: Path) -> list[DataSourceRecord]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ValueError(f"Invalid source manifest: {path}")
    return [DataSourceRecord.model_validate(item) for item in raw["sources"]]


def write_manifest(path: Path, records: list[DataSourceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "sources": [record.model_dump(mode="json") for record in records],
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
