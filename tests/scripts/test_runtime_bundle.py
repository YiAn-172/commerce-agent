from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.runtime_bundle import BUNDLE_MANIFEST, import_bundle, sha256_file, write_bundle


def make_runtime_files(root: Path) -> None:
    model = root / "models/intent_classifier/current/onnx/model.onnx"
    knowledge = root / "data/processed/knowledge/kb_20260917_001/documents.jsonl"
    model.parent.mkdir(parents=True)
    knowledge.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    knowledge.write_text('{"document_id":"doc-1"}\n', encoding="utf-8")


def test_runtime_bundle_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    make_runtime_files(source)
    bundle = tmp_path / "runtime.zip"

    manifest = write_bundle(source, bundle)
    result = import_bundle(destination, bundle)

    assert manifest["file_count"] == 2
    assert result["installed_files"] == 2
    assert sha256_file(destination / manifest["files"][0]["path"]) == manifest["files"][0]["sha256"]


def test_runtime_bundle_rejects_undeclared_member(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_runtime_files(source)
    bundle = tmp_path / "runtime.zip"
    write_bundle(source, bundle)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr("unexpected.txt", "unexpected")

    with pytest.raises(ValueError, match="exactly match"):
        import_bundle(tmp_path / "destination", bundle)


def test_runtime_bundle_rejects_path_traversal(tmp_path: Path) -> None:
    bundle = tmp_path / "runtime.zip"
    manifest = {
        "schema_version": "1.0",
        "files": [{"path": "../escape.txt", "size_bytes": 1, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("../escape.txt", "x")

    with pytest.raises(ValueError, match="unsafe bundle member"):
        import_bundle(tmp_path / "destination", bundle)
