from __future__ import annotations

import argparse
from pathlib import Path

from packages.data_pipeline.provenance import hash_directory, load_manifest
from packages.data_pipeline.schemas import LicenseStatus


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail closed on data provenance or license drift")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data_sources.yaml"))
    args = parser.parse_args()

    records = load_manifest(args.manifest)
    errors: list[str] = []
    for record in records:
        if record.license_status != LicenseStatus.APPROVED:
            errors.append(f"{record.source_id}: license status is {record.license_status}")
        local_path = Path(record.local_path)
        if not local_path.exists():
            errors.append(f"{record.source_id}: local path is missing: {local_path}")
            continue
        digest, file_count, total_bytes = hash_directory(local_path)
        if (digest, file_count, total_bytes) != (
            record.sha256,
            record.file_count,
            record.total_bytes,
        ):
            errors.append(f"{record.source_id}: local files do not match the recorded manifest")
        if record.source_id == "full_ecom" and not record.approved_record_sources:
            errors.append("full_ecom: approved_record_sources must fail closed")

    if errors:
        raise SystemExit("Data license/provenance gate failed:\n- " + "\n- ".join(errors))
    print(f"License/provenance gate passed for {len(records)} sources")


if __name__ == "__main__":
    main()
