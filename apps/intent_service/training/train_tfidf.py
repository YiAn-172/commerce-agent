from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from apps.intent_service.training.common import (
    LABEL2ID,
    LABELS,
    classification_metrics,
    environment_report,
    labels_to_ids,
    load_split,
    stable_file_manifest,
    write_json,
)
from packages.data_pipeline.provenance import sha256_file

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("human_handoff", ("人工客服", "转人工", "真人客服", "找人工")),
    ("complaint", ("投诉", "欺诈", "假货", "骗子", "曝光")),
    ("refund_progress", ("退款到账", "退款进度", "退款到哪", "钱退了吗")),
    ("logistics_tracking", ("物流", "快递", "配送到哪", "预计送达", "轨迹")),
    ("cancel_order", ("取消订单", "订单不要", "撤销订单")),
    ("order_status", ("订单状态", "发货了吗", "怎么还没发货", "支付成功")),
    ("product_compare", ("哪个好", "对比", "区别", "哪个更", "相比")),
    ("stock_price", ("库存", "有货", "多少钱", "价格", "优惠价")),
    ("return_exchange", ("退货", "换货", "维修", "我要退款")),
    ("after_sales_eligibility", ("能退吗", "可以换吗", "退换期限", "符合退货")),
    ("policy_faq", ("发票", "运费", "保修", "支付方式", "活动规则")),
    ("product_recommend", ("推荐", "适合我", "买什么", "怎么选")),
    ("product_search", ("帮我找", "想买", "有没有", "找一款")),
    ("product_detail", ("参数", "规格", "兼容", "怎么用")),
    ("chitchat", ("你好", "谢谢", "再见", "辛苦了")),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train reproducible intent baselines")
    parser.add_argument("--data-version", default="intent_v1")
    parser.add_argument("--data-root", type=Path, default=Path("data/processed/intent_v1"))
    parser.add_argument("--data-manifest", type=Path, default=Path("data/manifests/intent_v1.json"))
    parser.add_argument(
        "--model-output",
        type=Path,
        default=Path("models/intent_classifier/tfidf_v1.joblib"),
    )
    parser.add_argument("--report", type=Path, default=Path("reports/training/tfidf_v1.json"))
    return parser.parse_args()


def predict_rules(texts: list[str]) -> np.ndarray:
    predictions: list[int] = []
    for text in texts:
        normalized = re.sub(r"\s+", "", text.casefold())
        selected = "out_of_scope"
        for label, keywords in RULES:
            if any(keyword in normalized for keyword in keywords):
                selected = label
                break
        predictions.append(LABEL2ID[selected])
    return np.asarray(predictions, dtype=np.int64)


def hard_predictions_to_logits(predictions: np.ndarray) -> np.ndarray:
    logits = np.full((len(predictions), len(LABELS)), -8.0, dtype=np.float64)
    logits[np.arange(len(predictions)), predictions] = 8.0
    return logits


def evaluate_model(
    model: Pipeline,
    texts: list[str],
    y_true: np.ndarray,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    logits = np.asarray(model.decision_function(texts), dtype=np.float64)
    elapsed = time.perf_counter() - started
    metrics = classification_metrics(y_true, logits)
    metrics["examples_per_second"] = len(texts) / elapsed
    metrics["decision_scores_are_uncalibrated"] = True
    return metrics, elapsed


def write_html(report_path: Path, report: dict[str, Any]) -> None:
    html_path = report_path.with_suffix(".html")
    rows = []
    for name in ("rule_baseline", "tfidf_linearsvc"):
        metrics = report[name]["test"]
        rows.append(
            "<tr>"
            f"<td>{name}</td><td>{metrics['accuracy']:.4f}</td>"
            f"<td>{metrics['macro_f1']:.4f}</td>"
            f"<td>{metrics['top2_accuracy']:.4f}</td>"
            "</tr>"
        )
    html = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<title>Intent Baselines</title><body><h1>Intent Baselines</h1>"
        "<table border='1'><tr><th>model</th><th>accuracy</th>"
        "<th>macro_f1</th><th>top2_accuracy</th></tr>" + "".join(rows) + "</table></body></html>"
    )
    html_path.write_text(html, encoding="utf-8")


def main() -> None:
    args = parse_args()
    train = load_split(args.data_root, "train")
    validation = load_split(args.data_root, "validation")
    test = load_split(args.data_root, "test")
    train_texts = train["text_zh"].astype(str).tolist()
    y_train = labels_to_ids(train["target_intent"].astype(str).tolist())
    validation_texts = validation["text_zh"].astype(str).tolist()
    y_validation = labels_to_ids(validation["target_intent"].astype(str).tolist())
    test_texts = test["text_zh"].astype(str).tolist()
    y_test = labels_to_ids(test["target_intent"].astype(str).tolist())

    pipeline = Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        (
                            "char",
                            TfidfVectorizer(
                                analyzer="char",
                                ngram_range=(1, 5),
                                min_df=2,
                                sublinear_tf=True,
                                max_features=250_000,
                            ),
                        ),
                        (
                            "word",
                            TfidfVectorizer(
                                analyzer="word",
                                ngram_range=(1, 2),
                                min_df=2,
                                sublinear_tf=True,
                                max_features=100_000,
                            ),
                        ),
                    ]
                ),
            ),
            ("classifier", LinearSVC(C=1.0, class_weight="balanced", random_state=20260915)),
        ]
    )
    started = time.perf_counter()
    pipeline.fit(train_texts, y_train)
    training_seconds = time.perf_counter() - started
    validation_metrics, _ = evaluate_model(pipeline, validation_texts, y_validation)
    test_metrics, _ = evaluate_model(pipeline, test_texts, y_test)

    rule_validation = classification_metrics(
        y_validation, hard_predictions_to_logits(predict_rules(validation_texts))
    )
    rule_test = classification_metrics(
        y_test, hard_predictions_to_logits(predict_rules(test_texts))
    )

    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"pipeline": pipeline, "labels": LABELS, "data_version": args.data_version},
        args.model_output,
    )
    manifest = json.loads(args.data_manifest.read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": "tfidf_v1",
        "data_version": args.data_version,
        "data_manifest_sha256": sha256_file(args.data_manifest),
        "split_sha256": manifest["output_sha256"],
        "label_order": LABELS,
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows": len(test),
        "rule_baseline": {"validation": rule_validation, "test": rule_test},
        "tfidf_linearsvc": {
            "training_seconds": training_seconds,
            "validation": validation_metrics,
            "test": test_metrics,
        },
        "model_path": args.model_output.as_posix(),
        "model_sha256": sha256_file(args.model_output),
        "input_files": stable_file_manifest(
            [
                args.data_root / "train.parquet",
                args.data_root / "validation.parquet",
                args.data_root / "test.parquet",
            ]
        ),
        "environment": environment_report(),
        "limitations": [
            "Metrics use the internal development test split, not the pending human gold set.",
            "LinearSVC decision scores are converted with softmax only for diagnostics "
            "and are not calibrated probabilities.",
        ],
    }
    write_json(args.report, report)
    write_html(args.report, report)
    printable = {
        "rule_test_accuracy": rule_test["accuracy"],
        "rule_test_macro_f1": rule_test["macro_f1"],
        "tfidf_test_accuracy": test_metrics["accuracy"],
        "tfidf_test_macro_f1": test_metrics["macro_f1"],
        "training_seconds": training_seconds,
        "model_sha256": report["model_sha256"],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
