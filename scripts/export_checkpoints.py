from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def serializable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    return value


def export_database(path: Path, output: Any, *, all_history: bool) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "status": "missing", "tables": {}, "rows": 0}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    table_counts: dict[str, int] = {}
    total = 0
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            if not table.replace("_", "").isalnum():
                raise ValueError(f"unsafe SQLite table name: {table}")
            query = f'SELECT * FROM "{table}"'  # noqa: S608
            if not all_history and table == "checkpoints":
                query = (
                    "WITH latest AS ("
                    "SELECT MAX(rowid) AS latest_rowid FROM checkpoints "
                    "GROUP BY thread_id, checkpoint_ns"
                    ") SELECT checkpoints.* FROM checkpoints "
                    "JOIN latest ON checkpoints.rowid = latest.latest_rowid"
                )
            elif not all_history and table == "writes":
                query = (
                    "WITH latest AS ("
                    "SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints "
                    "WHERE rowid IN (SELECT MAX(rowid) FROM checkpoints "
                    "GROUP BY thread_id, checkpoint_ns)"
                    ") SELECT writes.* FROM writes JOIN latest USING "
                    "(thread_id, checkpoint_ns, checkpoint_id)"
                )
            count = 0
            for row in connection.execute(query):
                output.write(
                    json.dumps(
                        {
                            "database": path.name,
                            "table": table,
                            "row": {key: serializable(value) for key, value in dict(row).items()},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                count += 1
            table_counts[table] = count
            total += count
    finally:
        connection.close()
    return {"path": str(path), "status": "exported", "tables": table_counts, "rows": total}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export LangGraph SQLite checkpoints")
    parser.add_argument(
        "--database",
        action="append",
        type=Path,
        default=[],
        help="repeatable SQLite checkpoint path",
    )
    parser.add_argument("--output", type=Path, default=Path("backups/checkpoints.jsonl"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/release/checkpoint_backup.json"),
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help="export every historical checkpoint instead of the latest resumable state per thread",
    )
    args = parser.parse_args()
    databases = args.database or [
        Path(os.getenv("CHECKPOINT_DB", "/cache/checkpoints/checkpoints.sqlite")),
        Path(
            os.getenv(
                "APPROVAL_CHECKPOINT_DB",
                "/cache/checkpoints/approval-checkpoints.sqlite",
            )
        ),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        results = [
            export_database(path, output, all_history=args.all_history) for path in databases
        ]
    exported = [item for item in results if item["status"] == "exported"]
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    report = {
        "schema_version": "1.0",
        "evaluation_status": "verified" if exported else "failed",
        "output": str(args.output),
        "output_sha256": digest,
        "mode": "all_history" if args.all_history else "latest_state_per_thread",
        "databases": results,
        "total_rows": sum(int(item["rows"]) for item in exported),
        "created_at": datetime.now(UTC).isoformat(),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["evaluation_status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
