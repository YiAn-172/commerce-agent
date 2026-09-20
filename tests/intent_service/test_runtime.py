from __future__ import annotations

from dataclasses import replace

import numpy as np

from apps.intent_service.runtime import (
    IntentRuntime,
    RuntimeMetadata,
    is_multi_intent,
    stable_softmax,
)
from packages.contracts.intent import HighLevelRoute, IntentLabel


def make_runtime() -> IntentRuntime:
    runtime = object.__new__(IntentRuntime)
    runtime.metadata = RuntimeMetadata(
        model_version="test-v1",
        labels=[label.value for label in IntentLabel],
        temperature=1.0,
        auto_route_min_confidence=0.78,
        clarify_below_confidence=0.55,
        route_margin=0.20,
        max_length=128,
        oos_mean=(0.0, 0.0, 0.0),
        oos_scale=(1.0, 1.0, 1.0),
        oos_coefficients=(0.0, 0.0, 0.0),
        oos_intercept=-20.0,
        oos_threshold=0.5,
    )
    runtime.route_map = {
        label: (
            HighLevelRoute.HUMAN
            if label in {IntentLabel.COMPLAINT, IntentLabel.HUMAN_HANDOFF}
            else HighLevelRoute.SAFE_REPLY
            if label == IntentLabel.OUT_OF_SCOPE
            else HighLevelRoute.GENERAL
        )
        for label in IntentLabel
    }
    return runtime


def probabilities_for(label: IntentLabel, confidence: float) -> np.ndarray:
    labels = [item.value for item in IntentLabel]
    remainder = (1.0 - confidence) / (len(labels) - 1)
    probabilities = np.full(len(labels), remainder, dtype=np.float64)
    probabilities[labels.index(label.value)] = confidence
    return probabilities


def test_softmax_is_normalized_and_temperature_aware() -> None:
    logits = np.asarray([[2.0, 1.0, 0.0]], dtype=np.float64)
    cold = stable_softmax(logits, 0.5)
    warm = stable_softmax(logits, 2.0)
    assert np.isclose(cold.sum(), 1.0)
    assert cold.max() > warm.max()


def test_multi_intent_requires_connector_and_distinct_action_groups() -> None:
    assert is_multi_intent("帮我查物流，另外我要退货") is True
    assert is_multi_intent("帮我查一下物流") is False
    assert is_multi_intent("另外帮我查查订单") is False


def test_priority_human_rule_overrides_model_and_keeps_model_label() -> None:
    runtime = make_runtime()
    probabilities = probabilities_for(IntentLabel.ORDER_STATUS, 0.90)
    result = runtime._build_prediction(
        "不要机器人，给我转人工客服",
        np.log(probabilities),
        probabilities,
    )
    assert result.label == IntentLabel.HUMAN_HANDOFF
    assert result.model_label == IntentLabel.ORDER_STATUS
    assert result.route == HighLevelRoute.HUMAN
    assert result.matched_rule == "explicit_human_handoff"
    assert result.route_source == "priority_rule"


def test_low_confidence_is_not_force_routed() -> None:
    runtime = make_runtime()
    probabilities = probabilities_for(IntentLabel.PRODUCT_SEARCH, 0.30)
    result = runtime._build_prediction(
        "我想买一个东西",
        np.log(probabilities),
        probabilities,
    )
    assert result.decision == "safe_reply"
    assert result.route == HighLevelRoute.SAFE_REPLY
    assert result.route_source == "confidence_gate"


def test_oos_ensemble_can_override_primary_classifier() -> None:
    runtime = make_runtime()
    runtime.metadata = replace(runtime.metadata, oos_intercept=20.0)
    probabilities = probabilities_for(IntentLabel.CHITCHAT, 0.95)
    result = runtime._build_prediction(
        "解释一个项目范围之外的问题",
        np.log(probabilities),
        probabilities,
    )
    assert result.model_label == IntentLabel.CHITCHAT
    assert result.label == IntentLabel.OUT_OF_SCOPE
    assert result.route == HighLevelRoute.SAFE_REPLY
    assert result.route_source == "oos_detector"
    assert result.oos_score > 0.99
