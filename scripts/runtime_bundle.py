from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

BUNDLE_MANIFEST = "runtime-bundle-manifest.json"
DEFAULT_PATHS = (
    Path("models/intent_classifier/current"),
    Path("data/processed/knowledge/kb_20260917_001"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_files(root: Path, paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[Path]:
    files: list[Path] = []
    for relative in paths:
        candidate = root / relative
        if not candidate.exists():
            raise FileNotFoundError(f"required runtime artifact is missing: {relative}")
        files.extend(path for path in candidate.rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_manifest(root: Path, files: list[Path]) -> dict[str, Any]:
    entries = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        entries.append(
            {"path": relative, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    return {
        "schema_version": "1.0",
        "bundle_type": "commerce_agent_demo_runtime",
        "file_count": len(entries),
        "total_size_bytes": sum(entry["size_bytes"] for entry in entries),
        "files": entries,
    }


def write_bundle(root: Path, output: Path) -> dict[str, Any]:
    files = selected_files(root)
    manifest = build_manifest(root, files)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(
            BUNDLE_MANIFEST,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    return manifest


def _safe_destination(root: Path, member: str) -> Path:
    pure = PurePosixPath(member)
    if pure.is_absolute() or ".." in pure.parts or member == BUNDLE_MANIFEST:
        raise ValueError(f"unsafe bundle member: {member}")
    destination = (root / Path(*pure.parts)).resolve()
    destination.relative_to(root.resolve())
    return destination


def read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(BUNDLE_MANIFEST))
    except KeyError as exc:
        raise ValueError("runtime bundle manifest is missing") from exc
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise ValueError("runtime bundle manifest is invalid")
    return value


def import_bundle(root: Path, bundle: Path, *, replace: bool = False) -> dict[str, Any]:
    with zipfile.ZipFile(bundle) as archive:
        manifest = read_manifest(archive)
        entries = manifest["files"]
        declared = {entry["path"] for entry in entries}
        actual = {name for name in archive.namelist() if name != BUNDLE_MANIFEST}
        if actual != declared:
            raise ValueError("bundle members do not exactly match the manifest")

        installed = 0
        skipped = 0
        for entry in entries:
            destination = _safe_destination(root, entry["path"])
            if destination.exists() and sha256_file(destination) == entry["sha256"]:
                skipped += 1
                continue
            if destination.exists() and not replace:
                raise FileExistsError(
                    f"runtime artifact differs and --replace was not provided: {entry['path']}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry["path"]) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if destination.stat().st_size != entry["size_bytes"]:
                raise ValueError(f"size verification failed: {entry['path']}")
            if sha256_file(destination) != entry["sha256"]:
                raise ValueError(f"SHA-256 verification failed: {entry['path']}")
            installed += 1

    return {
        "manifest": manifest,
        "installed_files": installed,
        "skipped_identical_files": skipped,
    }
