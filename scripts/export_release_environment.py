from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SENSITIVE_KEY = re.compile(r"(password|secret|token|api[_-]?key|dsn)$", re.IGNORECASE)


def run(*arguments: str) -> bytes:
    result = subprocess.run(arguments, capture_output=True, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"command failed ({arguments[0]}): {error}")
    return result.stdout


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(child_key): redact(child, str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, key) for child in value]
    return value


def records(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    return [
        item
        for line in text.splitlines()
        if line.strip()
        for item in [json.loads(line)]
        if isinstance(item, dict)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export redacted Docker release evidence")
    parser.add_argument(
        "--compose-output",
        type=Path,
        default=Path("reports/release/compose.resolved.yaml"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/release/docker_environment.json"),
    )
    args = parser.parse_args()

    compose_raw = run(
        "docker",
        "compose",
        "--profile",
        "demo-full",
        "--profile",
        "test",
        "config",
    )
    compose_document = yaml.safe_load(compose_raw)
    redacted_document = redact(compose_document)
    redacted_yaml = yaml.safe_dump(
        redacted_document,
        allow_unicode=True,
        sort_keys=True,
    )
    args.compose_output.parent.mkdir(parents=True, exist_ok=True)
    args.compose_output.write_text(redacted_yaml, encoding="utf-8")

    image_rows = records(run("docker", "compose", "images", "--format", "json"))
    images: list[dict[str, Any]] = []
    for row in image_rows:
        image_id = str(row.get("ID") or row.get("ImageID") or "")
        repo_digests: list[str] = []
        if image_id:
            inspected = json.loads(
                run(
                    "docker",
                    "image",
                    "inspect",
                    image_id,
                    "--format",
                    "{{json .RepoDigests}}",
                ).decode("utf-8")
            )
            if isinstance(inspected, list):
                repo_digests = [str(item) for item in inspected]
        images.append(
            {
                "service": row.get("Service"),
                "repository": row.get("Repository"),
                "tag": row.get("Tag"),
                "image_id": image_id,
                "repo_digests": repo_digests,
                "size": row.get("Size"),
            }
        )

    container_ids = run("docker", "compose", "ps", "-q").decode().split()
    resources = (
        records(
            run(
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *container_ids,
            )
        )
        if container_ids
        else []
    )
    docker_version = run("docker", "version", "--format", "{{json .Server}}").decode().strip()
    report = {
        "schema_version": "1.0",
        "evaluation_status": "verified" if images and container_ids else "failed",
        "compose_output": str(args.compose_output),
        "compose_resolved_sha256": hashlib.sha256(compose_raw).hexdigest(),
        "compose_redacted_sha256": hashlib.sha256(redacted_yaml.encode()).hexdigest(),
        "secrets_redacted": True,
        "docker_server": json.loads(docker_version),
        "images": images,
        "resources": resources,
        "created_at": datetime.now(UTC).isoformat(),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "evaluation_status": report["evaluation_status"],
                "compose_output": str(args.compose_output),
                "images": len(images),
                "containers": len(container_ids),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["evaluation_status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
