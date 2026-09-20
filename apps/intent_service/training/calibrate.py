from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_recall_fscore_support, roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from apps.intent_service.training.common import (
    LABEL2ID,
    classification_metrics,
    load_split,
    softmax,
    write_json,
)
from apps.intent_service.training.train import make_loader, predict_logits
from packages.data_pipeline.provenance import sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Temperature-calibrate the best MacBERT")
    parser.add_argument("--checkpoint", type=Path, default=Path("models/intent_classifier/best"))
    parser.add_argument("--config", type=Path, default=Path("configs/intent/macbert_v1.yaml"))
    parser.add_argument("--report", type=Path, default=Path("reports/training/calibration_v1.json"))
    return parser.parse_args()


def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    logits_tensor = torch.tensor(logits, dtype=torch.float64)
    labels_tensor = torch.tensor(labels, dtype=torch.long)
    log_temperature = torch.nn.Parameter(torch.zeros((), dtype=torch.float64))
    optimizer = torch.optim.LBFGS(
        [log_temperature], lr=0.1, max_iter=100, line_search_fn="strong_wolfe"
    )

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        temperature = torch.exp(log_temperature).clamp(0.05, 20.0)
        loss = torch.nn.functional.cross_entropy(logits_tensor / temperature, labels_tensor)
        loss.backward()  # type: ignore[no-untyped-call]
        return loss

    optimizer.step(closure)  # type: ignore[no-untyped-call]
    value = float(torch.exp(log_temperature).detach().clamp(0.05, 20.0))
    if not math.isfinite(value):
        raise RuntimeError("Temperature optimization produced a non-finite value")
    return value


def coverage_risk_curve(labels: np.ndarray, probabilities: np.ndarray) -> list[dict[str, Any]]:
    predictions = probabilities.argmax(axis=1)
    confidences = probabilities.max(axis=1)
    rows: list[dict[str, Any]] = []
    for threshold in np.linspace(0.0, 1.0, 201):
        accepted = confidences >= threshold
        coverage = float(accepted.mean())
        if np.any(accepted):
            accuracy = float(np.mean(predictions[accepted] == labels[accepted]))
            risk = 1.0 - accuracy
            accepted_rows = int(accepted.sum())
        else:
            accuracy = 1.0
            risk = 0.0
            accepted_rows = 0
        rows.append(
            {
                "threshold": float(threshold),
                "coverage": coverage,
                "selective_accuracy": accuracy,
                "risk": risk,
                "accepted_rows": accepted_rows,
            }
        )
    return rows


def routing_grid(labels: np.ndarray, probabilities: np.ndarray) -> list[dict[str, Any]]:
    predictions = probabilities.argmax(axis=1)
    sorted_probabilities = np.sort(probabilities, axis=1)
    confidences = sorted_probabilities[:, -1]
    margins = sorted_probabilities[:, -1] - sorted_probabilities[:, -2]
    rows: list[dict[str, Any]] = []
    for confidence_threshold in np.linspace(0.0, 1.0, 101):
        for margin_threshold in np.linspace(0.0, 0.5, 26):
            accepted = (confidences >= confidence_threshold) & (margins >= margin_threshold)
            coverage = float(accepted.mean())
            accuracy = (
                float(np.mean(predictions[accepted] == labels[accepted]))
                if np.any(accepted)
                else 1.0
            )
            rows.append(
                {
                    "confidence_threshold": float(confidence_threshold),
                    "margin_threshold": float(margin_threshold),
                    "coverage": coverage,
                    "selective_accuracy": accuracy,
                    "accepted_rows": int(accepted.sum()),
                }
            )
    return rows


def oos_features(logits: np.ndarray, temperature: float) -> np.ndarray:
    probabilities = softmax(logits, temperature)
    maximum = np.max(logits, axis=1, keepdims=True)
    energy = -(maximum[:, 0] + np.log(np.exp(logits - maximum).sum(axis=1)))
    return np.column_stack(
        [
            probabilities[:, LABEL2ID["out_of_scope"]],
            probabilities.max(axis=1),
            energy,
        ]
    )


def fit_oos_detector(
    validation_logits: np.ndarray,
    validation_labels: np.ndarray,
    test_logits: np.ndarray,
    test_labels: np.ndarray,
    temperature: float,
) -> dict[str, Any]:
    validation_x = oos_features(validation_logits, temperature)
    test_x = oos_features(test_logits, temperature)
    mean = validation_x.mean(axis=0)
    scale = validation_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    validation_scaled = (validation_x - mean) / scale
    test_scaled = (test_x - mean) / scale
    validation_y = (validation_labels == LABEL2ID["out_of_scope"]).astype(np.int64)
    test_y = (test_labels == LABEL2ID["out_of_scope"]).astype(np.int64)
    classifier = LogisticRegression(
        class_weight="balanced",
        random_state=20260915,
        max_iter=1000,
    ).fit(validation_scaled, validation_y)
    validation_scores = classifier.predict_proba(validation_scaled)[:, 1]
    test_scores = classifier.predict_proba(test_scaled)[:, 1]
    candidates: list[tuple[float, float]] = []
    for threshold in np.linspace(0.0, 1.0, 1001):
        score = float(f1_score(validation_y, validation_scores >= threshold, zero_division=0))
        candidates.append((score, float(threshold)))
    _, threshold = max(candidates, key=lambda item: (item[0], item[1]))

    def metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
        predictions = scores >= threshold
        precision, recall, f1, _ = precision_recall_fscore_support(
            labels,
            predictions,
            average="binary",
            zero_division=0,
        )
        return {
            "auroc": float(roc_auc_score(labels, scores)),
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "positive_rows": int(labels.sum()),
        }

    return {
        "feature_order": ["oos_probability", "max_probability", "energy_score"],
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "coefficients": classifier.coef_[0].tolist(),
        "intercept": float(classifier.intercept_[0]),
        "threshold": threshold,
        "validation": metrics(validation_y, validation_scores),
        "internal_test": metrics(test_y, test_scores),
    }


def main() -> None:
    args = parse_args()
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if not (args.checkpoint / "model.safetensors").exists():
        raise SystemExit(f"Checkpoint is incomplete: {args.checkpoint}")
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(args.checkpoint).to(device)
    use_amp = device.type == "cuda" and config["mixed_precision"] == "fp16"
    frames = {
        split: load_split(Path(config["data_root"]), split) for split in ("validation", "test")
    }
    outputs: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for split, frame in frames.items():
        loader = make_loader(
            frame,
            tokenizer,
            max_length=int(config["max_length"]),
            batch_size=int(config["eval_batch_size"]),
            shuffle=False,
            seed=20260915,
            num_workers=int(config["num_workers"]),
        )
        logits, labels, _ = predict_logits(model, loader, device, use_amp)
        outputs[split] = (logits, labels)

    validation_logits, validation_labels = outputs["validation"]
    temperature = fit_temperature(validation_logits, validation_labels)
    oos_detector = fit_oos_detector(
        validation_logits,
        validation_labels,
        outputs["test"][0],
        outputs["test"][1],
        temperature,
    )
    report = {
        "schema_version": "1.0",
        "checkpoint": args.checkpoint.as_posix(),
        "checkpoint_selection": json.loads(
            (args.checkpoint / "selection.json").read_text(encoding="utf-8")
        ),
        "config_sha256": sha256_file(args.config),
        "temperature": temperature,
        "oos_detector": oos_detector,
        "validation": {
            "before": classification_metrics(validation_labels, validation_logits),
            "after": classification_metrics(
                validation_labels, validation_logits, temperature=temperature
            ),
            "coverage_risk_curve": coverage_risk_curve(
                validation_labels, softmax(validation_logits, temperature)
            ),
            "routing_grid": routing_grid(
                validation_labels, softmax(validation_logits, temperature)
            ),
        },
        "internal_test": {
            "before": classification_metrics(outputs["test"][1], outputs["test"][0]),
            "after": classification_metrics(
                outputs["test"][1], outputs["test"][0], temperature=temperature
            ),
        },
        "limitations": [
            "Temperature and routing thresholds are fitted on validation only.",
            "The reported test set is an internal development split, not human gold.",
        ],
    }
    write_json(
        args.checkpoint / "calibration.json",
        {"temperature": temperature, "oos_detector": oos_detector},
    )
    write_json(args.report, report)
    print(
        json.dumps(
            {
                "temperature": temperature,
                "validation_ece_before": report["validation"]["before"]["ece_15_bin"],
                "validation_ece_after": report["validation"]["after"]["ece_15_bin"],
                "test_ece_after": report["internal_test"]["after"]["ece_15_bin"],
                "oos_validation": oos_detector["validation"],
                "oos_internal_test": oos_detector["internal_test"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
