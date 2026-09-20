from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
import torch
import yaml
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from apps.intent_service.training.common import (
    LABELS,
    directory_sha256,
    load_split,
    write_json,
)
from packages.data_pipeline.provenance import sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify ONNX parity and publish a runtime model")
    parser.add_argument("--checkpoint", type=Path, default=Path("models/intent_classifier/best"))
    parser.add_argument(
        "--onnx", type=Path, default=Path("models/intent_classifier/best/onnx/model.onnx")
    )
    parser.add_argument("--config", type=Path, default=Path("configs/intent/macbert_v1.yaml"))
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=Path("configs/routing/thresholds.intent_v1.yaml"),
    )
    parser.add_argument("--publish", type=Path, default=Path("models/intent_classifier/current"))
    parser.add_argument("--report", type=Path, default=Path("reports/training/onnx_verify_v1.json"))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--samples", type=int, default=2500)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--min-top1-agreement", type=float, default=0.999)
    return parser.parse_args()


def session_inputs(
    encoded: dict[str, torch.Tensor], input_names: set[str]
) -> dict[str, np.ndarray]:
    values: dict[str, np.ndarray] = {}
    for name in input_names:
        if name == "token_type_ids" and name not in encoded:
            values[name] = np.zeros_like(encoded["input_ids"].cpu().numpy())
        else:
            values[name] = encoded[name].cpu().numpy()
    return values


def percentile(values: list[float], quantile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), quantile))


def publish_runtime(
    checkpoint: Path,
    onnx_path: Path,
    thresholds: Path,
    destination: Path,
    verification: dict[str, Any],
    max_length: int,
) -> None:
    model_root = Path("models/intent_classifier").resolve()
    destination_resolved = destination.resolve()
    if destination_resolved.parent != model_root:
        raise ValueError(f"Refusing to replace unsafe publish path: {destination}")
    staging = destination.with_name(destination.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    runtime_files = [
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
        "calibration.json",
        "selection.json",
    ]
    for name in runtime_files:
        source = checkpoint / name
        if source.exists():
            shutil.copy2(source, staging / name)
    (staging / "onnx").mkdir()
    shutil.copy2(onnx_path, staging / "onnx" / "model.onnx")
    shutil.copy2(thresholds, staging / "thresholds.yaml")
    manifest = {
        "schema_version": "1.0",
        "model_version": "intent-macbert-v1",
        "max_length": max_length,
        "labels": LABELS,
        "source_checkpoint": checkpoint.as_posix(),
        "source_checkpoint_sha256": directory_sha256(checkpoint),
        "onnx_sha256": sha256_file(onnx_path),
        "thresholds_sha256": sha256_file(thresholds),
        "verification": verification,
    }
    write_json(staging / "model_manifest.json", manifest)
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)


def main() -> None:
    args = parse_args()
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    missing = [
        path
        for path in (args.onnx, args.thresholds, args.checkpoint / "calibration.json")
        if not path.exists()
    ]
    if missing:
        raise SystemExit(f"Required artifacts are missing: {missing}")

    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pytorch_model = AutoModelForSequenceClassification.from_pretrained(args.checkpoint).to(device)
    pytorch_model.eval()
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 2
    session_options.inter_op_num_threads = 1
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(args.onnx),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    input_names = {item.name for item in session.get_inputs()}
    frame = load_split(Path(config["data_root"]), "test")
    if args.samples > len(frame):
        raise SystemExit(
            f"Requested {args.samples} verification rows, but the test split has {len(frame)}"
        )
    frame = frame.head(args.samples)
    texts = frame["text_zh"].astype(str).tolist()
    pytorch_predictions: list[int] = []
    onnx_predictions: list[int] = []
    maximum_difference = 0.0
    absolute_difference_sum = 0.0
    logit_count = 0
    for start in range(0, len(texts), args.batch_size):
        batch = texts[start : start + args.batch_size]
        encoded = tokenizer(
            batch,
            return_tensors="pt",
            max_length=int(config["max_length"]),
            truncation=True,
            padding=True,
        )
        onnx_logits = np.asarray(session.run(["logits"], session_inputs(encoded, input_names))[0])
        with torch.inference_mode():
            device_batch = {key: value.to(device) for key, value in encoded.items()}
            pytorch_logits = pytorch_model(**device_batch).logits.detach().float().cpu().numpy()
        difference = np.abs(pytorch_logits - onnx_logits)
        maximum_difference = max(maximum_difference, float(difference.max()))
        absolute_difference_sum += float(difference.sum())
        logit_count += int(difference.size)
        pytorch_predictions.extend(np.argmax(pytorch_logits, axis=1).tolist())
        onnx_predictions.extend(np.argmax(onnx_logits, axis=1).tolist())

    agreement = float(np.mean(np.asarray(pytorch_predictions) == np.asarray(onnx_predictions)))
    benchmark_text = "我的订单已经发货了吗，帮我查一下物流"

    def benchmark_once() -> None:
        benchmark_encoded = tokenizer(
            benchmark_text,
            return_tensors="pt",
            max_length=int(config["max_length"]),
            truncation=True,
            padding=True,
        )
        benchmark_inputs = session_inputs(benchmark_encoded, input_names)
        session.run(["logits"], benchmark_inputs)

    for _ in range(args.warmup):
        benchmark_once()
    latencies_ms: list[float] = []
    for _ in range(args.iterations):
        started = time.perf_counter_ns()
        benchmark_once()
        elapsed = time.perf_counter_ns() - started
        latencies_ms.append(elapsed / 1_000_000)

    verification = {
        "test_rows": len(texts),
        "top1_agreement": agreement,
        "required_top1_agreement": args.min_top1_agreement,
        "max_absolute_logit_difference": maximum_difference,
        "mean_absolute_logit_difference": absolute_difference_sum / logit_count,
        "cpu_benchmark": {
            "warmup_iterations": args.warmup,
            "measured_iterations": args.iterations,
            "batch_size": 1,
            "scope": "tokenization_plus_onnx_session",
            "intra_op_threads": 2,
            "inter_op_threads": 1,
            "p50_ms": percentile(latencies_ms, 50),
            "p95_ms": percentile(latencies_ms, 95),
            "p99_ms": percentile(latencies_ms, 99),
            "mean_ms": float(np.mean(latencies_ms)),
        },
        "passed": agreement >= args.min_top1_agreement,
    }
    report = {
        "schema_version": "1.0",
        "onnx_path": args.onnx.as_posix(),
        "onnx_sha256": sha256_file(args.onnx),
        "checkpoint": args.checkpoint.as_posix(),
        "providers": session.get_providers(),
        "device_for_pytorch_reference": str(device),
        "verification": verification,
    }
    write_json(args.report, report)
    if not verification["passed"]:
        raise SystemExit(
            f"ONNX top-1 agreement {agreement:.6f} is below {args.min_top1_agreement:.6f}"
        )
    publish_runtime(
        args.checkpoint,
        args.onnx,
        args.thresholds,
        args.publish,
        verification,
        int(config["max_length"]),
    )
    report["published_path"] = args.publish.as_posix()
    report["published_sha256"] = directory_sha256(args.publish)
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
