from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.runtime_bundle import import_bundle, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and import demo runtime artifacts")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--sha256")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--report", type=Path, default=Path("reports/release/runtime_bundle_import.json")
    )
    args = parser.parse_args()

    root = args.root.resolve()
    bundle = args.bundle.resolve()
    observed_sha256 = sha256_file(bundle)
    if args.sha256 and observed_sha256.lower() != args.sha256.lower():
        raise SystemExit("runtime bundle SHA-256 does not match --sha256")
    result = import_bundle(root, bundle, replace=args.replace)
    report = {
        "schema_version": "1.0",
        "evaluation_status": "verified",
        "bundle_sha256": observed_sha256,
        "file_count": result["manifest"]["file_count"],
        "installed_files": result["installed_files"],
        "skipped_identical_files": result["skipped_identical_files"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    report_path = (root / args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
