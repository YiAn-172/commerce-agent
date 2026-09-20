from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import torch
import yaml
from huggingface_hub import HfApi
from torch.utils.data import DataLoader, Dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from apps.intent_service.training.common import (
    ID2LABEL,
    LABEL2ID,
    LABELS,
    classification_metrics,
    environment_report,
    float_slug,
    labels_to_ids,
    load_split,
    set_global_seed,
    write_json,
)
from packages.data_pipeline.provenance import sha256_file


class IntentDataset(Dataset[tuple[str, int]]):
    def __init__(self, texts: list[str], labels: np.ndarray) -> None:
        self.texts = texts
        self.labels = labels

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, index: int) -> tuple[str, int]:
        return self.texts[index], int(self.labels[index])


@dataclass
class BatchCollator:
    tokenizer: Any
    max_length: int

    def __call__(self, examples: list[tuple[str, int]]) -> dict[str, torch.Tensor]:
        texts, labels = zip(*examples, strict=True)
        encoded = self.tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        encoded["labels"] = torch.tensor(labels, dtype=torch.long)
        return cast(dict[str, torch.Tensor], encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fine-tune MacBERT for 16-class intent routing")
    parser.add_argument("--config", type=Path, default=Path("configs/intent/macbert_v1.yaml"))
    parser.add_argument("--smoke", action="store_true", help="Run a tiny pipeline smoke test")
    return parser.parse_args()


def make_loader(
    frame: pd.DataFrame,
    tokenizer: Any,
    *,
    max_length: int,
    batch_size: int,
    shuffle: bool,
    seed: int,
    num_workers: int,
) -> DataLoader[dict[str, torch.Tensor]]:
    dataset = IntentDataset(
        frame["text_zh"].astype(str).tolist(),
        labels_to_ids(frame["target_intent"].astype(str).tolist()),
    )
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(
        cast(Any, dataset),
        batch_size=batch_size,
        shuffle=shuffle,
        generator=generator,
        num_workers=num_workers,
        pin_memory=torch.cuda.is_available(),
        collate_fn=BatchCollator(tokenizer, max_length),
        persistent_workers=num_workers > 0,
    )


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


def predict_logits(
    model: torch.nn.Module,
    loader: DataLoader[dict[str, torch.Tensor]],
    device: torch.device,
    use_amp: bool,
) -> tuple[np.ndarray, np.ndarray, float]:
    model.eval()
    logits_parts: list[np.ndarray] = []
    labels_parts: list[np.ndarray] = []
    started = time.perf_counter()
    with torch.no_grad():
        for batch in loader:
            batch = move_batch(batch, device)
            labels = batch.pop("labels")
            with torch.amp.autocast(
                device_type=device.type,
                dtype=torch.float16,
                enabled=use_amp,
            ):
                logits = model(**batch).logits
            logits_parts.append(logits.float().cpu().numpy())
            labels_parts.append(labels.cpu().numpy())
    elapsed = time.perf_counter() - started
    return np.concatenate(logits_parts), np.concatenate(labels_parts), elapsed


def tokenizer_length_report(
    tokenizer: Any, frames: list[pd.DataFrame], max_length: int
) -> dict[str, Any]:
    lengths: list[int] = []
    texts: list[str] = []
    for frame in frames:
        texts.extend(frame["text_zh"].astype(str).tolist())
    for start in range(0, len(texts), 512):
        encoded = tokenizer(
            texts[start : start + 512],
            add_special_tokens=True,
            truncation=False,
            return_length=True,
        )
        lengths.extend(int(value) for value in encoded["length"])
    values = np.asarray(lengths, dtype=np.int64)
    coverage = float(np.mean(values <= max_length))
    return {
        "rows": len(values),
        "max_length": max_length,
        "coverage": coverage,
        "truncated_rows": int(np.sum(values > max_length)),
        "p50": float(np.quantile(values, 0.50)),
        "p95": float(np.quantile(values, 0.95)),
        "p99": float(np.quantile(values, 0.99)),
        "max": int(values.max()),
    }


def load_completed_run(run_dir: Path) -> dict[str, Any] | None:
    metrics_path = run_dir / "run_metrics.json"
    model_path = run_dir / "model.safetensors"
    if metrics_path.exists() and model_path.exists():
        return cast(dict[str, Any], json.loads(metrics_path.read_text(encoding="utf-8")))
    return None


def train_one_run(
    *,
    config: dict[str, Any],
    tokenizer: Any,
    resolved_revision: str,
    train_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    seed: int,
    learning_rate: float,
    smoke: bool,
) -> dict[str, Any]:
    output_root = Path(config["output_root"])
    run_id = f"seed{seed}_lr{float_slug(learning_rate)}"
    if smoke:
        run_id += "_smoke"
    run_dir = output_root / "runs" / run_id
    completed = load_completed_run(run_dir)
    if completed is not None:
        print(f"[{run_id}] existing completed run reused")
        return completed

    set_global_seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = device.type == "cuda" and config["mixed_precision"] == "fp16"
    if smoke:
        train_frame = train_frame.head(128)
        validation_frame = validation_frame.head(128)
    train_loader = make_loader(
        train_frame,
        tokenizer,
        max_length=int(config["max_length"]),
        batch_size=int(config["train_batch_size"]),
        shuffle=True,
        seed=seed,
        num_workers=0 if smoke else int(config["num_workers"]),
    )
    validation_loader = make_loader(
        validation_frame,
        tokenizer,
        max_length=int(config["max_length"]),
        batch_size=int(config["eval_batch_size"]),
        shuffle=False,
        seed=seed,
        num_workers=0 if smoke else int(config["num_workers"]),
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        config["model_name"],
        revision=resolved_revision,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=learning_rate,
        weight_decay=float(config["weight_decay"]),
    )
    accumulation = int(config["gradient_accumulation_steps"])
    epochs = 1 if smoke else int(config["epochs"])
    steps_per_epoch = math.ceil(len(train_loader) / accumulation)
    total_steps = steps_per_epoch * epochs
    warmup_steps = int(total_steps * float(config["warmup_ratio"]))
    scheduler = get_linear_schedule_with_warmup(  # type: ignore[no-untyped-call]
        optimizer, warmup_steps, total_steps
    )
    scaler = torch.amp.GradScaler(device.type, enabled=use_amp)
    best_metric = -1.0
    best_epoch = 0
    patience = 0
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    for epoch in range(1, epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        running_loss = 0.0
        for step, batch in enumerate(train_loader, start=1):
            batch = move_batch(batch, device)
            with torch.amp.autocast(
                device_type=device.type,
                dtype=torch.float16,
                enabled=use_amp,
            ):
                loss = model(**batch).loss / accumulation
            scaler.scale(loss).backward()
            running_loss += float(loss.detach().cpu()) * accumulation
            if step % accumulation == 0 or step == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), float(config["max_grad_norm"]))
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                scheduler.step()

        validation_logits, validation_labels, validation_seconds = predict_logits(
            model, validation_loader, device, use_amp
        )
        validation_metrics = classification_metrics(validation_labels, validation_logits)
        epoch_record = {
            "epoch": epoch,
            "train_loss": running_loss / len(train_loader),
            "learning_rate": scheduler.get_last_lr()[0],
            "validation_seconds": validation_seconds,
            "validation": validation_metrics,
        }
        history.append(epoch_record)
        current = float(validation_metrics[str(config["metric_for_best_model"])])
        print(
            f"[{run_id}] epoch={epoch} loss={epoch_record['train_loss']:.5f} "
            f"val_macro_f1={validation_metrics['macro_f1']:.5f}"
        )
        if current > best_metric + 1e-6:
            best_metric = current
            best_epoch = epoch
            patience = 0
            run_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(run_dir, safe_serialization=True)
            tokenizer.save_pretrained(run_dir)
        else:
            patience += 1
            if patience >= int(config["early_stopping_patience"]):
                break

    training_seconds = time.perf_counter() - started
    result: dict[str, Any] = {
        "run_id": run_id,
        "seed": seed,
        "learning_rate": learning_rate,
        "base_model": config["model_name"],
        "base_model_revision": resolved_revision,
        "best_epoch": best_epoch,
        "best_validation_macro_f1": best_metric,
        "training_seconds": training_seconds,
        "device": str(device),
        "mixed_precision": use_amp,
        "peak_gpu_memory_bytes": (
            int(torch.cuda.max_memory_allocated()) if device.type == "cuda" else 0
        ),
        "history": history,
        "checkpoint": run_dir.as_posix(),
        "status": "smoke_complete" if smoke else "complete",
    }
    write_json(run_dir / "run_metrics.json", result)
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return result


def evaluate_checkpoint(
    checkpoint: Path,
    test_frame: pd.DataFrame,
    config: dict[str, Any],
    tokenizer: Any,
    seed: int,
) -> dict[str, Any]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = device.type == "cuda" and config["mixed_precision"] == "fp16"
    loader = make_loader(
        test_frame,
        tokenizer,
        max_length=int(config["max_length"]),
        batch_size=int(config["eval_batch_size"]),
        shuffle=False,
        seed=seed,
        num_workers=int(config["num_workers"]),
    )
    model = AutoModelForSequenceClassification.from_pretrained(checkpoint).to(device)
    logits, labels, elapsed = predict_logits(model, loader, device, use_amp)
    metrics = classification_metrics(labels, logits)
    metrics["evaluation_seconds"] = elapsed
    metrics["examples_per_second"] = len(test_frame) / elapsed
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return metrics


def safe_replace_directory(source: Path, target: Path, allowed_root: Path) -> None:
    resolved_target = target.resolve()
    resolved_root = allowed_root.resolve()
    if resolved_root not in resolved_target.parents:
        raise RuntimeError(f"Refusing to replace path outside {resolved_root}: {resolved_target}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def main() -> None:
    args = parse_args()
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if int(config["num_labels"]) != len(LABELS):
        raise SystemExit(f"Config num_labels must be {len(LABELS)}")
    data_root = Path(config["data_root"])
    train_frame = load_split(data_root, "train")
    validation_frame = load_split(data_root, "validation")
    test_frame = load_split(data_root, "test")
    if args.smoke:
        test_frame = test_frame.head(128)

    requested_revision = str(config["model_revision"])
    resolved_revision = (
        HfApi().model_info(str(config["model_name"]), revision=requested_revision).sha
    )
    if not resolved_revision:
        raise RuntimeError("Unable to resolve MacBERT model revision")
    tokenizer = AutoTokenizer.from_pretrained(
        config["model_name"], revision=resolved_revision, use_fast=True
    )
    length_report = tokenizer_length_report(
        tokenizer, [train_frame, validation_frame, test_frame], int(config["max_length"])
    )
    if length_report["coverage"] < 0.99:
        raise RuntimeError(f"max_length coverage {length_report['coverage']:.4f} is below 0.99")

    lr_seed = int(config["lr_search_seed"])
    lr_results = [
        train_one_run(
            config=config,
            tokenizer=tokenizer,
            resolved_revision=resolved_revision,
            train_frame=train_frame,
            validation_frame=validation_frame,
            seed=lr_seed,
            learning_rate=float(learning_rate),
            smoke=args.smoke,
        )
        for learning_rate in config["learning_rates"][: 1 if args.smoke else None]
    ]
    selected_lr_result = max(lr_results, key=lambda item: float(item["best_validation_macro_f1"]))
    selected_lr = float(selected_lr_result["learning_rate"])
    final_runs: list[dict[str, Any]] = []
    for seed_raw in config["seeds"][: 1 if args.smoke else None]:
        seed = int(seed_raw)
        existing = next(
            (
                result
                for result in lr_results
                if int(result["seed"]) == seed and float(result["learning_rate"]) == selected_lr
            ),
            None,
        )
        result = existing or train_one_run(
            config=config,
            tokenizer=tokenizer,
            resolved_revision=resolved_revision,
            train_frame=train_frame,
            validation_frame=validation_frame,
            seed=seed,
            learning_rate=selected_lr,
            smoke=args.smoke,
        )
        result["internal_test"] = evaluate_checkpoint(
            Path(result["checkpoint"]), test_frame, config, tokenizer, seed
        )
        write_json(Path(result["checkpoint"]) / "run_metrics.json", result)
        final_runs.append(result)

    metrics = ("accuracy", "macro_f1", "top2_accuracy", "ece_15_bin")
    aggregate: dict[str, dict[str, float]] = {}
    for metric in metrics:
        values = np.asarray(
            [float(run["internal_test"][metric]) for run in final_runs], dtype=np.float64
        )
        aggregate[metric] = {
            "mean": float(values.mean()),
            "std": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
        }

    best_run = max(final_runs, key=lambda item: float(item["best_validation_macro_f1"]))
    if not args.smoke:
        safe_replace_directory(
            Path(best_run["checkpoint"]),
            Path(config["best_output"]),
            Path("models/intent_classifier"),
        )
        write_json(
            Path(config["best_output"]) / "selection.json",
            {
                "run_group": config["run_group"],
                "selection_metric": config["metric_for_best_model"],
                "selected_run_id": best_run["run_id"],
                "selected_seed": best_run["seed"],
                "selected_learning_rate": selected_lr,
                "base_model_revision": resolved_revision,
            },
        )

    report = {
        "schema_version": "1.0",
        "run_group": config["run_group"],
        "status": "smoke_complete" if args.smoke else "complete",
        "config_sha256": sha256_file(args.config),
        "data_manifest_sha256": sha256_file(Path(config["data_manifest"])),
        "base_model": config["model_name"],
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
        "model_license": config["model_license"],
        "label_order": LABELS,
        "token_length": length_report,
        "lr_search": lr_results,
        "selected_learning_rate": selected_lr,
        "final_seed_runs": final_runs,
        "aggregate_internal_test": aggregate,
        "selected_run_id": best_run["run_id"],
        "best_output": None if args.smoke else config["best_output"],
        "environment": environment_report(),
        "limitations": [
            "Metrics use the internal development test split, not the pending human gold set.",
            "The deployment checkpoint is selected only by validation macro-F1.",
        ],
    }
    report_path = Path(config["report"])
    if args.smoke:
        report_path = report_path.with_name("macbert_v1_smoke.json")
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "resolved_revision": resolved_revision,
                "token_length": length_report,
                "selected_learning_rate": selected_lr,
                "aggregate_internal_test": aggregate,
                "selected_run_id": best_run["run_id"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
