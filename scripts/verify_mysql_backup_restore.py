from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

SAFE_DATABASE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
TABLES = ("users", "orders", "service_tickets", "approval_tasks", "knowledge_versions")


def compose_exec(command: str, *, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "mysql", "sh", "-c", command],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        summary = result.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"mysql container command failed: {summary}")
    return result.stdout


def query_counts(database_expression: str) -> dict[str, int]:
    statements = ";".join(f"SELECT COUNT(*) FROM {table}" for table in TABLES) + ";"
    output = compose_exec(
        f"mysql -N -uroot -p\"$MYSQL_ROOT_PASSWORD\" {database_expression} -e '{statements}'"
    ).decode("utf-8")
    values = [int(line.strip()) for line in output.splitlines() if line.strip().isdigit()]
    if len(values) != len(TABLES):
        raise RuntimeError("unexpected row-count output from restored database")
    return dict(zip(TABLES, values, strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up MySQL and restore it into an isolated temporary database"
    )
    parser.add_argument("--backup", type=Path, default=Path("backups/commerce.sql"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/release/mysql_backup_restore.json"),
    )
    args = parser.parse_args()
    temporary_database = f"commerce_restore_{uuid4().hex[:12]}"
    if not SAFE_DATABASE.fullmatch(temporary_database):
        raise SystemExit("generated unsafe temporary database name")
    args.backup.parent.mkdir(parents=True, exist_ok=True)

    dump = compose_exec(
        "mysqldump --single-transaction --routines --triggers "
        '-uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
    )
    args.backup.write_bytes(dump)
    source_counts = query_counts('"$MYSQL_DATABASE"')
    restored_counts: dict[str, int] = {}
    try:
        compose_exec(
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
            f'-e "CREATE DATABASE {temporary_database} CHARACTER SET utf8mb4"'
        )
        compose_exec(
            f'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "{temporary_database}"',
            input_bytes=dump,
        )
        restored_counts = query_counts(f'"{temporary_database}"')
    finally:
        compose_exec(
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
            f'-e "DROP DATABASE IF EXISTS {temporary_database}"'
        )

    matched = source_counts == restored_counts
    report = {
        "schema_version": "1.0",
        "evaluation_status": "verified" if matched else "failed",
        "restore_target": "isolated_temporary_database_deleted_after_verification",
        "backup": str(args.backup),
        "backup_bytes": len(dump),
        "backup_sha256": hashlib.sha256(dump).hexdigest(),
        "source_counts": source_counts,
        "restored_counts": restored_counts,
        "counts_match": matched,
        "created_at": datetime.now(UTC).isoformat(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not matched:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
