from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.runtime_bundle import import_bundle, sha256_file


def run(command: list[str], *, cwd: Path, timeout: int) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    ended_at = datetime.now(UTC)
    return {
        "command": command,
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def safe_recreate(directory: Path, *, root: Path) -> None:
    resolved = directory.resolve()
    allowed_parent = (root / ".tmp").resolve()
    if resolved.parent != allowed_parent or resolved.name != "fresh-clone-verification":
        raise ValueError(f"refusing to recreate unexpected directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the committed source from a clean local clone without claiming "
            "runtime-artifact reproduction"
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--clone-dir", type=Path, default=Path(".tmp/fresh-clone-verification")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/release/fresh_clone_reproduction.json"),
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--runtime-bundle",
        type=Path,
        help="Optional verified artifact bundle to import into the clean clone.",
    )
    parser.add_argument("--runtime-bundle-sha256")
    parser.add_argument(
        "--skip-quality",
        action="store_true",
        help="Only clone and validate Compose; do not run Ruff, mypy, or pytest.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    clone_dir = (root / args.clone_dir).resolve()
    safe_recreate(clone_dir, root=root)

    checks: list[dict[str, Any]] = []
    checks.append(
        run(
            ["git", "clone", "--local", "--no-hardlinks", str(root), str(clone_dir)],
            cwd=root,
            timeout=args.timeout,
        )
    )
    if not checks[-1]["passed"]:
        raise SystemExit("fresh clone failed")

    source_env = root / ".env"
    env_source_kind = "workspace_env"
    if not source_env.exists():
        source_env = clone_dir / ".env.example"
        env_source_kind = "example_env"
    if source_env.exists():
        shutil.copyfile(source_env, clone_dir / ".env")

    bundle_result: dict[str, Any] | None = None
    bundle_sha256: str | None = None
    if args.runtime_bundle is not None:
        bundle = args.runtime_bundle.resolve()
        bundle_sha256 = sha256_file(bundle)
        if (
            args.runtime_bundle_sha256
            and bundle_sha256.lower() != args.runtime_bundle_sha256.lower()
        ):
            raise SystemExit("runtime bundle SHA-256 does not match")
        bundle_result = import_bundle(clone_dir, bundle)

    checks.append(run(["git", "status", "--porcelain"], cwd=clone_dir, timeout=60))
    clone_clean = checks[-1]["passed"] and not checks[-1]["stdout_tail"].strip()
    checks.append(run(["docker", "compose", "config", "--quiet"], cwd=clone_dir, timeout=120))

    cache_dir = root / ".uv-cache"
    if not args.skip_quality:
        quality_commands = [
            [
                "uv",
                "run",
                "--cache-dir",
                str(cache_dir),
                "--frozen",
                "--all-groups",
                "ruff",
                "check",
                "packages",
                "apps",
                "services",
                "evals",
                "scripts",
                "tests",
            ],
            [
                "uv",
                "run",
                "--cache-dir",
                str(cache_dir),
                "--frozen",
                "--all-groups",
                "mypy",
                "packages",
                "apps",
                "services",
                "evals",
                "scripts",
                "tests",
            ],
            [
                "docker",
                "compose",
                "--profile",
                "test",
                "run",
                "--rm",
                "--no-deps",
                "evaluator",
                "uv",
                "run",
                "--frozen",
                "pytest",
                "-q",
                "tests",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                "/tmp/pytest-fresh-clone",
            ],
        ]
        for command in quality_commands:
            checks.append(run(command, cwd=clone_dir, timeout=args.timeout))

    head = run(["git", "rev-parse", "HEAD"], cwd=clone_dir, timeout=60)
    checks.append(head)
    runtime_artifacts = {
        "intent_model_manifest": (
            clone_dir / "models/intent_classifier/current/model_manifest.json"
        ).exists(),
        "intent_onnx": (clone_dir / "models/intent_classifier/current/onnx/model.onnx").exists(),
        "knowledge_documents": (
            clone_dir / "data/processed/knowledge/kb_20260917_001/documents.jsonl"
        ).exists(),
    }
    training_artifacts = {
        "intent_train_data": (
            clone_dir / "data/processed/intent_v1/train.parquet"
        ).exists(),
    }
    source_checks_passed = clone_clean and all(check["passed"] for check in checks)
    runtime_artifacts_complete = all(runtime_artifacts.values())
    if source_checks_passed and runtime_artifacts_complete:
        evaluation_status = "source_and_runtime_artifacts_verified"
    elif source_checks_passed:
        evaluation_status = "source_verified_runtime_artifacts_pending"
    else:
        evaluation_status = "failed"

    report = {
        "schema_version": "1.0",
        "evaluation_status": evaluation_status,
        "git_commit": head["stdout_tail"].strip() if head["passed"] else None,
        "clone_clean": clone_clean,
        "source_checks_passed": source_checks_passed,
        "runtime_artifacts_complete": runtime_artifacts_complete,
        "runtime_artifacts": runtime_artifacts,
        "training_artifacts": training_artifacts,
        "runtime_bundle": {
            "sha256": bundle_sha256,
            "file_count": (
                bundle_result["manifest"]["file_count"] if bundle_result is not None else None
            ),
            "installed_files": (
                bundle_result["installed_files"] if bundle_result is not None else 0
            ),
        },
        "full_isolated_demo_stack_started": False,
        "scope_note": (
            "This verifies committed source plus any explicitly supplied runtime bundle from a "
            "clean local clone. It does not claim a fully isolated demo-stack deployment."
        ),
        "checks": checks,
        "environment": {
            "operating_system": os.name,
            "clone_path": str(clone_dir),
            "configuration_source": env_source_kind,
            "pytest_dependencies": "existing_compose_network",
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not source_checks_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
