from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from apps.intent_service.runtime import IntentRuntime
from apps.intent_service.training.common import (
    LABEL2ID,
    LABELS,
    expected_calibration_error,
    multiclass_brier,
    softmax,
    write_json,
)
from packages.data_pipeline.provenance import sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate TF-IDF and published ONNX on a frozen labeled intent suite"
    )
    parser.add_argument("--gold", type=Path, default=Path("data/processed/intent_gold_v1.parquet"))
    parser.add_argument(
        "--gold-manifest",
        type=Path,
        default=Path("data/manifests/intent_gold_v1.json"),
    )
    parser.add_argument("--runtime", type=Path, default=Path("models/intent_classifier/current"))
    parser.add_argument(
        "--tfidf",
        type=Path,
        default=Path("models/intent_classifier/tfidf_v1.joblib"),
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/training/gold_evaluation_v1.json")
    )
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def label_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    label_ids = np.arange(len(LABELS))
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=label_ids,
        zero_division=0,
    )
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(np.mean(precision)),
        "macro_recall": float(np.mean(recall)),
        "macro_f1": float(np.mean(f1)),
        "ece_15_bin": expected_calibration_error(y_true, probabilities),
        "multiclass_brier": multiclass_brier(y_true, probabilities),
        "oos_f1": float(
            f1_score(
                y_true == LABEL2ID["out_of_scope"],
                y_pred == LABEL2ID["out_of_scope"],
                zero_division=0,
            )
        ),
        "per_class": {
            label: {
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
                "support": int(support[index]),
            }
            for index, label in enumerate(LABELS)
        },
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=label_ids).tolist(),
    }


def main() -> None:
    args = parse_args()
    missing = [path for path in (args.gold, args.gold_manifest, args.tfidf) if not path.exists()]
    if missing:
        raise SystemExit(
            "Frozen intent evaluation is blocked; required artifacts are missing: "
            + ", ".join(str(path) for path in missing)
        )
    manifest: dict[str, Any] = json.loads(args.gold_manifest.read_text(encoding="utf-8"))
    if manifest.get("row_count") != 1600:
        raise SystemExit("Frozen intent manifest must contain exactly 1,600 rows")
    if manifest.get("output_sha256") != sha256_file(args.gold):
        raise SystemExit("Frozen intent hash does not match its manifest")
    evaluation_status = manifest.get("evaluation_status", "human_gold_verified")
    if evaluation_status not in {
        "human_gold_verified",
        "ai_assisted_verified_for_demo",
    }:
        raise SystemExit(f"Unsupported intent evidence status: {evaluation_status}")

    frame = pd.read_parquet(args.gold)
    texts = frame["text_zh"].astype(str).tolist()
    labels = frame["target_intent"].astype(str).tolist()
    if len(frame) != 1600 or set(labels) != set(LABELS):
        raise SystemExit("Frozen intent suite has an invalid row count or label set")
    y_true = np.asarray([LABEL2ID[label] for label in labels], dtype=np.int64)

    tfidf_payload: dict[str, Any] = joblib.load(args.tfidf)
    tfidf_pipeline = tfidf_payload["pipeline"]
    tfidf_classes = np.asarray(tfidf_pipeline.classes_, dtype=np.int64)
    if not np.array_equal(tfidf_classes, np.arange(len(LABELS), dtype=np.int64)):
        raise SystemExit("TF-IDF class order does not match the intent contract")
    tfidf_predictions = np.asarray(tfidf_pipeline.predict(texts), dtype=np.int64)
    tfidf_scores = np.asarray(tfidf_pipeline.decision_function(texts), dtype=np.float64)
    tfidf_probabilities = softmax(tfidf_scores)

    runtime = IntentRuntime(args.runtime)
    onnx_predictions: list[int] = []
    onnx_probability_rows: list[np.ndarray] = []
    error_samples: list[dict[str, Any]] = []
    for start in range(0, len(texts), args.batch_size):
        batch_texts = texts[start : start + args.batch_size]
        predictions = runtime.predict_batch(batch_texts)
        onnx_probability_rows.extend(runtime.predict_probabilities_batch(batch_texts))
        for offset, prediction in enumerate(predictions):
            if prediction.model_label is None:
                raise RuntimeError("Runtime response omitted the underlying model label")
            predicted_id = LABEL2ID[prediction.model_label.value]
            onnx_predictions.append(predicted_id)
            actual_id = int(y_true[start + offset])
            if predicted_id != actual_id:
                error_samples.append(
                    {
                        "sample_id": str(frame.iloc[start + offset]["sample_id"]),
                        "text_zh": batch_texts[offset],
                        "actual": LABELS[actual_id],
                        "predicted": prediction.model_label.value,
                        "confidence": prediction.confidence,
                        "second_candidate": prediction.candidates[1].label.value,
                    }
                )

    report = {
        "schema_version": "1.0",
        "gold_version": manifest["gold_version"],
        "gold_rows": len(frame),
        "gold_sha256": sha256_file(args.gold),
        "tfidf": label_metrics(y_true, tfidf_predictions, tfidf_probabilities),
        "macbert_onnx": label_metrics(
            y_true,
            np.asarray(onnx_predictions, dtype=np.int64),
            np.asarray(onnx_probability_rows, dtype=np.float64),
        ),
        "macbert_error_samples": error_samples,
        "evaluation_status": evaluation_status,
        "human_review_status": manifest.get("human_review_status", "complete"),
        "limitations": [
            (
                "TF-IDF ECE and Brier use softmax-normalized LinearSVC decision scores; "
                "they are diagnostic and not calibrated probabilities."
            ),
            "MacBERT ECE and Brier use the published validation-fitted temperature.",
            (
                "AI-assisted suites are project-owner-accepted demo evidence, not independent "
                "human-gold evaluation."
            ),
        ],
    }
    write_json(args.report, report)
    print(
        json.dumps(
            {
                "gold_rows": len(frame),
                "tfidf_accuracy": report["tfidf"]["accuracy"],
                "tfidf_macro_f1": report["tfidf"]["macro_f1"],
                "macbert_accuracy": report["macbert_onnx"]["accuracy"],
                "macbert_macro_f1": report["macbert_onnx"]["macro_f1"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
