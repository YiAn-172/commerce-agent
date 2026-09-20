from __future__ import annotations

import numpy as np

from apps.intent_service.training.common import LABELS
from apps.intent_service.training.evaluate_gold import label_metrics


def test_gold_metrics_include_calibration_and_oos() -> None:
    class_count = len(LABELS)
    oos_id = LABELS.index("out_of_scope")
    y_true = np.asarray([0, oos_id], dtype=np.int64)
    y_pred = np.asarray([0, oos_id], dtype=np.int64)
    probabilities = np.full((2, class_count), 0.0, dtype=np.float64)
    probabilities[0, 0] = 1.0
    probabilities[1, oos_id] = 1.0

    metrics = label_metrics(y_true, y_pred, probabilities)

    assert metrics["accuracy"] == 1.0
    assert metrics["oos_f1"] == 1.0
    assert metrics["ece_15_bin"] == 0.0
    assert metrics["multiclass_brier"] == 0.0
