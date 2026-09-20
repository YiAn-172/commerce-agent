from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datasets import DatasetDict, load_dataset
from huggingface_hub import HfApi

from packages.data_pipeline.provenance import (
    hash_directory,
    load_source_config,
    write_manifest,
)
from packages.data_pipeline.schemas import DataSourceRecord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and pin public intent sources")
    parser.add_argument("--config", type=Path, default=Path("configs/intent/data_sources.yaml"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data_sources.yaml"))
    parser.add_argument("--source", action="append", help="Download only selected source IDs")
    return parser.parse_args()


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return completed.stdout.strip()


def download_huggingface(source_id: str, spec: dict[str, Any], destination: Path) -> str:
    repo_id = str(spec["repo_id"])
    info = HfApi().dataset_info(repo_id, revision=str(spec["requested_revision"]))
    if not info.sha:
        raise RuntimeError(f"Hugging Face did not resolve a revision for {repo_id}")

    if destination.exists() and any(destination.iterdir()):
        revision_file = destination / "RESOLVED_REVISION"
        saved_revision = (
            revision_file.read_text(encoding="utf-8").strip() if revision_file.exists() else None
        )
        if saved_revision != info.sha:
            raise RuntimeError(
                f"{destination} already exists with another/unknown revision; review it manually"
            )
        print(f"[{source_id}] already downloaded at {info.sha}; reusing")
        return info.sha

    destination.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(repo_id, revision=info.sha)
    if not isinstance(dataset, DatasetDict):
        dataset = DatasetDict({"train": dataset})
    dataset.save_to_disk(destination / "dataset")
    (destination / "RESOLVED_REVISION").write_text(f"{info.sha}\n", encoding="utf-8")
    print(f"[{source_id}] downloaded {sum(len(split) for split in dataset.values())} rows")
    return info.sha


def download_git(source_id: str, spec: dict[str, Any], destination: Path) -> str:
    requested = str(spec["requested_revision"])
    if destination.exists() and any(destination.iterdir()):
        if not (destination / ".git").exists():
            raise RuntimeError(f"{destination} exists but is not a Git checkout")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            "clone",
            "--filter=blob:none",
            "--branch",
            requested,
            str(spec["url"]),
            str(destination),
        )
    resolved = run_git("-C", str(destination), "rev-parse", "HEAD")
    print(f"[{source_id}] resolved Git revision {resolved}")
    return resolved


def build_record(
    source_id: str,
    spec: dict[str, Any],
    destination: Path,
    resolved_revision: str,
) -> DataSourceRecord:
    digest, file_count, total_bytes = hash_directory(destination)
    return DataSourceRecord(
        source_id=source_id,
        kind=spec["kind"],
        repo_id=spec.get("repo_id"),
        url=spec["url"],
        requested_revision=str(spec["requested_revision"]),
        resolved_revision=resolved_revision,
        downloaded_at=datetime.now(UTC).isoformat(),
        local_path=destination.as_posix(),
        declared_license=str(spec["expected_license"]),
        upstream_source=str(spec["upstream_source"]),
        allowed_use=str(spec["allowed_use"]),
        redistribution_allowed=bool(spec["redistribution_allowed"]),
        attribution_required=bool(spec["attribution_required"]),
        license_status=str(spec["license_status"]),
        approved_record_sources=list(spec.get("approved_record_sources", [])),
        handler=str(spec["handler"]),
        notes=str(spec["notes"]),
        file_count=file_count,
        total_bytes=total_bytes,
        sha256=digest,
    )


def main() -> None:
    args = parse_args()
    specs = load_source_config(args.config)
    selected = set(args.source or specs)
    unknown = selected.difference(specs)
    if unknown:
        raise SystemExit(f"Unknown source IDs: {sorted(unknown)}")

    records: list[DataSourceRecord] = []
    for source_id, spec in specs.items():
        if source_id not in selected:
            continue
        destination = args.raw_root / source_id
        if spec["kind"] == "huggingface_dataset":
            revision = download_huggingface(source_id, spec, destination)
        elif spec["kind"] == "git_repository":
            revision = download_git(source_id, spec, destination)
        else:
            raise RuntimeError(f"Unsupported source kind for {source_id}: {spec['kind']}")
        records.append(build_record(source_id, spec, destination, revision))

    if args.manifest.exists() and len(selected) != len(specs):
        from packages.data_pipeline.provenance import load_manifest

        previous = {item.source_id: item for item in load_manifest(args.manifest)}
        previous.update({item.source_id: item for item in records})
        records = [previous[source_id] for source_id in specs if source_id in previous]
    write_manifest(args.manifest, records)
    print(f"Wrote {len(records)} source records to {args.manifest}")


if __name__ == "__main__":
    main()
