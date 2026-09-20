from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import pandas as pd
import yaml
from datasets import DatasetDict, load_from_disk

from packages.contracts.intent import IntentLabel
from packages.data_pipeline.cleaning import clean_text
from packages.data_pipeline.provenance import load_manifest
from packages.data_pipeline.schemas import CandidateRecord, LicenseStatus


def load_mapping(path: Path) -> dict[str, dict[str, str]]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = raw["sources"]
    return cast(dict[str, dict[str, str]], sources)


def target_for(mapping: dict[str, str], source_intent: str) -> IntentLabel | None:
    target = mapping.get(source_intent, mapping.get("default", "discard"))
    return None if target == "discard" else IntentLabel(target)


def make_candidate(
    *,
    source_id: str,
    revision: str,
    row_id: str,
    text: str,
    language: str,
    source_intent: str,
    target_intent: IntentLabel,
    dialogue_id: str,
    provenance: str,
) -> CandidateRecord | None:
    cleaned = clean_text(text)
    if not 2 <= len(cleaned.text) <= 300:
        return None
    sample_key = f"{source_id}\0{revision}\0{row_id}"
    sample_id = hashlib.sha256(sample_key.encode()).hexdigest()[:24]
    return CandidateRecord(
        sample_id=sample_id,
        text_original=cleaned.text,
        text_zh=cleaned.text if language.lower().startswith("zh") else None,
        source_id=source_id,
        source_revision=revision,
        source_row_id=row_id,
        source_language=language,
        source_intent=source_intent,
        target_intent=target_intent,
        source_dialogue_id=dialogue_id,
        license_status=LicenseStatus.APPROVED,
        pii_status=cleaned.pii_status,
        quality_status="accepted",
        provenance=provenance,
    )


def iter_huggingface_rows(
    source_id: str,
    revision: str,
    raw_root: Path,
    mapping: dict[str, str],
    approved_record_sources: set[str],
) -> Iterable[CandidateRecord]:
    dataset = load_from_disk(raw_root / source_id / "dataset")
    if not isinstance(dataset, DatasetDict):
        dataset = DatasetDict({"train": dataset})
    for split_name, split in dataset.items():
        for index, row in enumerate(split):
            if source_id == "full_ecom":
                provenance = str(row["source"])
                if provenance not in approved_record_sources:
                    continue
                source_intent = str(row["intent"])
                text = str(row["prompt"])
                language = str(row.get("locale") or row.get("language") or "en")
                row_id = str(row.get("id") or f"{split_name}-{index}")
            elif source_id == "massive_zh":
                provenance = "amazon_massive"
                source_intent = str(row["label_text"])
                text = str(row["text"])
                language = "zh-CN"
                row_id = f"{split_name}-{row['id']}"
            elif source_id == "bitext_retail":
                provenance = "bitext_retail_direct"
                source_intent = str(row["intent"])
                text = str(row["instruction"])
                language = "en"
                row_id = f"{split_name}-{index}"
            else:
                continue
            target = target_for(mapping, source_intent)
            if target is None:
                continue
            candidate = make_candidate(
                source_id=source_id,
                revision=revision,
                row_id=row_id,
                text=text,
                language=language,
                source_intent=source_intent,
                target_intent=target,
                dialogue_id=f"{source_id}:{row_id}",
                provenance=provenance,
            )
            if candidate:
                yield candidate


def decode_smp(payload: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", payload, 0, 1, "unknown SMP2017 encoding")


def iter_smp_rows(
    revision: str, raw_root: Path, mapping: dict[str, str]
) -> Iterable[CandidateRecord]:
    archive = raw_root / "smp2017" / "LREC.zip"
    pattern = re.compile(r"Task1data/(train|develop|test)/\1_([a-z]+)\.txt$")
    with zipfile.ZipFile(archive) as handle:
        for member in sorted(handle.namelist()):
            match = pattern.search(member)
            if not match:
                continue
            split_name, source_intent = match.groups()
            target = target_for(mapping, source_intent)
            if target is None:
                continue
            content = decode_smp(handle.read(member))
            for index, text in enumerate(content.splitlines()):
                row_id = f"{split_name}-{source_intent}-{index}"
                candidate = make_candidate(
                    source_id="smp2017",
                    revision=revision,
                    row_id=row_id,
                    text=text,
                    language="zh-CN",
                    source_intent=source_intent,
                    target_intent=target,
                    dialogue_id=f"smp2017:{row_id}",
                    provenance="smp2017_task1",
                )
                if candidate:
                    yield candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize public sources into one schema")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--manifest", type=Path, default=Path("data/manifests/data_sources.yaml"))
    parser.add_argument("--mapping", type=Path, default=Path("configs/intent/label_mapping.yaml"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/intent_candidates.parquet")
    )
    args = parser.parse_args()

    mappings = load_mapping(args.mapping)
    records = {record.source_id: record for record in load_manifest(args.manifest)}
    candidates: list[CandidateRecord] = []
    for source_id in ("full_ecom", "massive_zh", "bitext_retail"):
        record = records[source_id]
        candidates.extend(
            iter_huggingface_rows(
                source_id,
                record.resolved_revision,
                args.raw_root,
                mappings[source_id],
                set(record.approved_record_sources),
            )
        )
        print(f"[{source_id}] candidate total so far: {len(candidates)}")
    smp = records["smp2017"]
    candidates.extend(iter_smp_rows(smp.resolved_revision, args.raw_root, mappings["smp2017"]))

    frame = pd.DataFrame([candidate.model_dump(mode="json") for candidate in candidates])
    if frame.empty:
        raise SystemExit("No candidates were produced")
    if frame["sample_id"].duplicated().any():
        raise SystemExit("Candidate sample_id collision detected")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output, index=False)
    summary = frame.groupby(["source_id", "target_intent"]).size().unstack(fill_value=0)
    print(summary.to_string())
    print(f"Wrote {len(frame)} candidates to {args.output}")


if __name__ == "__main__":
    main()
