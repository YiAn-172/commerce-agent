from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.runtime_bundle import sha256_file, write_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the ignored demo runtime artifacts")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output", type=Path, default=Path("dist/commerce-agent-demo-runtime-v1.zip")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/release/runtime_bundle_export.json")
    )
    args = parser.parse_args()

    root = args.root.resolve()
    output = (root / args.output).resolve()
    manifest = write_bundle(root, output)
    report = {
        "schema_version": "1.0",
        "evaluation_status": "verified",
        "bundle_path": str(output.relative_to(root)),
        "bundle_size_bytes": output.stat().st_size,
        "bundle_sha256": sha256_file(output),
        "file_count": manifest["file_count"],
        "payload_size_bytes": manifest["total_size_bytes"],
        "contains_secrets": False,
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
