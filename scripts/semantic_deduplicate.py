from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from datasketch import MinHash, MinHashLSH


def shingles(text: str, size: int = 3) -> set[str]:
    compact = "".join(text.lower().split())
    if len(compact) <= size:
        return {compact}
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def create_minhash(tokens: set[str], num_perm: int) -> MinHash:
    value = MinHash(num_perm=num_perm)
    for token in sorted(tokens):
        value.update(token.encode("utf-8"))
    return value


def minhash_filter(
    frame: pd.DataFrame, threshold: float, num_perm: int = 64
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    kept_indices: list[int] = []
    removed: list[dict[str, Any]] = []
    for label, group in frame.groupby("target_intent", sort=True):
        lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        token_by_key: dict[str, set[str]] = {}
        for index, row in group.sort_values("sample_id").iterrows():
            tokens = shingles(str(row["text_zh"]))
            signature = create_minhash(tokens, num_perm)
            matches = sorted(lsh.query(signature))
            duplicate_of: str | None = None
            for match in matches:
                union = tokens | token_by_key[match]
                score = len(tokens & token_by_key[match]) / len(union) if union else 1.0
                if score >= threshold:
                    duplicate_of = match
                    break
            if duplicate_of:
                removed.append(
                    {
                        "sample_id": row["sample_id"],
                        "reason": "minhash_near_duplicate",
                        "duplicate_of": duplicate_of,
                        "target_intent": label,
                    }
                )
                continue
            key = str(row["sample_id"])
            lsh.insert(key, signature)
            token_by_key[key] = tokens
            kept_indices.append(index)
    return frame.loc[kept_indices].copy(), removed


def semantic_filter(
    frame: pd.DataFrame,
    model_name: str,
    model_revision: str,
    threshold: float,
    batch_size: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
    model = AutoModel.from_pretrained(model_name, revision=model_revision)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    kept_indices: list[int] = []
    removed: list[dict[str, Any]] = []
    for label, group in frame.groupby("target_intent", sort=True):
        ordered = group.sort_values("sample_id")
        texts = ordered["text_zh"].astype(str).tolist()
        encoded_parts: list[np.ndarray] = []
        with torch.no_grad():
            for start in range(0, len(texts), batch_size):
                batch = tokenizer(
                    texts[start : start + batch_size],
                    padding=True,
                    truncation=True,
                    max_length=128,
                    return_tensors="pt",
                ).to(device)
                output = model(**batch).last_hidden_state[:, 0]
                normalized = torch.nn.functional.normalize(output, p=2, dim=1)
                encoded_parts.append(normalized.cpu().numpy())
        embeddings = np.concatenate(encoded_parts, axis=0)
        active = np.ones(len(ordered), dtype=bool)
        representative = np.arange(len(ordered))
        matrix = np.asarray(embeddings, dtype=np.float32)
        chunk_size = 256
        for start in range(0, len(ordered), chunk_size):
            end = min(start + chunk_size, len(ordered))
            similarities = matrix[start:end] @ matrix.T
            for local_index, scores in enumerate(similarities):
                index = start + local_index
                if not active[index]:
                    continue
                scores[: index + 1] = -1.0
                duplicates = np.flatnonzero((scores >= threshold) & active)
                for duplicate in duplicates:
                    active[duplicate] = False
                    representative[duplicate] = index
        ordered_indices = ordered.index.to_numpy()
        sample_ids = ordered["sample_id"].astype(str).to_numpy()
        for index, is_active in enumerate(active):
            if is_active:
                kept_indices.append(int(ordered_indices[index]))
            else:
                removed.append(
                    {
                        "sample_id": sample_ids[index],
                        "reason": "bge_semantic_duplicate",
                        "duplicate_of": sample_ids[representative[index]],
                        "target_intent": label,
                    }
                )
        for index in range(len(ordered)):
            representative_id = sample_ids[representative[index]]
            cluster_id = hashlib.sha256(representative_id.encode()).hexdigest()[:16]
            frame.loc[ordered_indices[index], "semantic_cluster_id"] = cluster_id
        print(f"[{label}] BGE checked {len(ordered)} rows; removed {int((~active).sum())}")
    return frame.loc[kept_indices].copy(), removed


def main() -> None:
    parser = argparse.ArgumentParser(description="MinHash and BGE semantic near-deduplication")
    parser.add_argument("--config", type=Path, default=Path("configs/intent/dataset_v1.yaml"))
    parser.add_argument(
        "--input", type=Path, default=Path("data/interim/quality_pool.exact.parquet")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/quality_pool.final.parquet")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data/near_duplicate_report.json")
    )
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--model-revision")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--bge-threshold", type=float)
    parser.add_argument("--skip-bge", action="store_true")
    args = parser.parse_args()

    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    quality = config["quality"]
    frame = pd.read_parquet(args.input)
    after_minhash, minhash_removed = minhash_filter(
        frame, threshold=float(quality["minhash_threshold"])
    )
    if args.skip_bge:
        final = after_minhash
        bge_removed: list[dict[str, Any]] = []
        bge_executed = False
        model_revision = None
    else:
        from huggingface_hub import HfApi

        model_revision = args.model_revision or HfApi().model_info(args.model).sha
        if not model_revision:
            raise RuntimeError(f"Unable to resolve model revision: {args.model}")
        bge_threshold = (
            args.bge_threshold
            if args.bge_threshold is not None
            else float(quality["bge_similarity_threshold"])
        )
        final, bge_removed = semantic_filter(
            after_minhash,
            args.model,
            model_revision,
            threshold=bge_threshold,
            batch_size=args.batch_size,
        )
        bge_executed = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    final.to_parquet(args.output, index=False)
    removals = minhash_removed + bge_removed
    report = {
        "input_rows": len(frame),
        "output_rows": len(final),
        "bge_executed": bge_executed,
        "bge_model": args.model if bge_executed else None,
        "bge_model_revision": model_revision,
        "minhash_threshold": float(quality["minhash_threshold"]),
        "bge_similarity_threshold": bge_threshold if bge_executed else None,
        "removal_counts": dict(Counter(item["reason"] for item in removals)),
        "removals": removals,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "removals"}, indent=2))


if __name__ == "__main__":
    main()
