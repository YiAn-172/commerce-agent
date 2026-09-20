from __future__ import annotations

import argparse
from pathlib import Path

from packages.data_pipeline.provenance import hash_directory, load_manifest, write_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute local source hashes")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data_sources.yaml"))
    args = parser.parse_args()

    records = load_manifest(args.manifest)
    for record in records:
        digest, file_count, total_bytes = hash_directory(Path(record.local_path))
        record.sha256 = digest
        record.file_count = file_count
        record.total_bytes = total_bytes
        print(f"[{record.source_id}] {digest} ({file_count} files, {total_bytes} bytes)")
    write_manifest(args.manifest, records)


if __name__ == "__main__":
    main()
