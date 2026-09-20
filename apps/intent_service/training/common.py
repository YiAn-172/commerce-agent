from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)

from packages.contracts.intent import IntentLabel
from packages.data_pipeline.provenance import sha256_file

LABELS = [label.value for label in IntentLabel]
LABEL2ID = {label: index for index, label in enumerate(LABELS)}
ID2LABEL = {index: label for label, index in LABEL2ID.items()}


def load_split(data_root: Path, split: str) -> pd.DataFrame:
    path = data_root / f"{split}.parquet"
    frame = pd.read_parquet(path)
    required = {"sample_id", "text_zh", "target_intent", "split"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
    if not set(frame["target_intent"].astype(str)).issubset(LABEL2ID):
        raise ValueError(f"{path} contains unknown intent labels")
    return frame


def labels_to_ids(values: list[str]) -> np.ndarray:
    return np.asarray([LABEL2ID[value] for value in values], dtype=np.int64)


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    if temperature <= 0 or not math.isfinite(temperature):
        raise ValueError("temperature must be a finite positive value")
    scaled = np.asarray(logits, dtype=np.float64) / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return np.asarray(
        exponentiated / exponentiated.sum(axis=1, keepdims=True),
        dtype=np.float64,
    )


def expected_calibration_error(
    y_true: np.ndarray, probabilities: np.ndarray, bins: int = 15
) -> float:
    predictions = probabilities.argmax(axis=1)
    confidences = probabilities.max(axis=1)
    correct = predictions == y_true
    boundaries = np.linspace(0.0, 1.0, bins + 1)
    value = 0.0
    for lower, upper in zip(boundaries[:-1], boundaries[1:], strict=True):
        in_bin = (confidences > lower) & (confidences <= upper)
        if not np.any(in_bin):
            continue
        value += float(in_bin.mean()) * abs(
            float(correct[in_bin].mean()) - float(confidences[in_bin].mean())
        )
    return value


def multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    one_hot = np.eye(len(LABELS), dtype=np.float64)[y_true]
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def negative_log_likelihood(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    chosen = probabilities[np.arange(len(y_true)), y_true]
    return float(-np.log(np.clip(chosen, 1e-12, 1.0)).mean())


def classification_metrics(
    y_true: np.ndarray,
    logits: np.ndarray,
    *,
    temperature: float = 1.0,
) -> dict[str, Any]:
    probabilities = softmax(logits, temperature)
    predictions = probabilities.argmax(axis=1)
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        predictions,
        labels=np.arange(len(LABELS)),
        zero_division=0,
    )
    top2 = np.argsort(probabilities, axis=1)[:, -2:]
    per_class = {
        label: {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
        for index, label in enumerate(LABELS)
    }
    return {
        "accuracy": float(accuracy_score(y_true, predictions)),
        "macro_precision": float(np.mean(precision)),
        "macro_recall": float(np.mean(recall)),
        "macro_f1": float(np.mean(f1)),
        "top2_accuracy": float(np.mean(np.any(top2 == y_true[:, None], axis=1))),
        "ece_15_bin": expected_calibration_error(y_true, probabilities),
        "multiclass_brier": multiclass_brier(y_true, probabilities),
        "negative_log_likelihood": negative_log_likelihood(y_true, probabilities),
        "per_class": per_class,
        "confusion_matrix": confusion_matrix(
            y_true, predictions, labels=np.arange(len(LABELS))
        ).tolist(),
    }


def set_global_seed(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except ImportError:
        pass


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stable_file_manifest(paths: list[Path]) -> dict[str, str]:
    return {path.as_posix(): sha256_file(path) for path in paths}


def directory_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def environment_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "created_unix_seconds": time.time(),
    }
    try:
        import torch

        report.update(
            {
                "torch": torch.__version__,
                "cuda_available": torch.cuda.is_available(),
                "cuda_version": torch.version.cuda,
                "gpu": (torch.cuda.get_device_name(0) if torch.cuda.is_available() else None),
            }
        )
    except ImportError:
        report["torch"] = None
    return report


def float_slug(value: float) -> str:
    return f"{value:.0e}".replace("-", "m").replace("+", "p")
