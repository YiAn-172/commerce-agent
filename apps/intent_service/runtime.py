from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import onnxruntime as ort
import yaml
from transformers import AutoTokenizer

from packages.contracts.intent import (
    HighLevelRoute,
    IntentCandidate,
    IntentLabel,
    IntentPrediction,
)

ACTION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("查", "找", "推荐", "比较", "对比", "参数", "库存", "价格"),
    ("订单", "发货", "物流", "快递", "取消"),
    ("退货", "换货", "退款", "维修", "售后"),
    ("投诉", "人工", "客服"),
)
CONNECTORS = ("同时", "另外", "还要", "还有", "并且", "以及", "然后", "顺便")
HUMAN_PATTERNS = ("转人工", "人工客服", "真人客服", "找人工", "人工服务")
COMPLAINT_PATTERNS = ("我要投诉", "严重投诉", "假货", "欺诈", "诈骗", "曝光你们")


def stable_softmax(logits: np.ndarray, temperature: float) -> np.ndarray:
    scaled = np.asarray(logits, dtype=np.float64) / temperature
    scaled -= np.max(scaled, axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return np.asarray(
        exponentiated / np.sum(exponentiated, axis=1, keepdims=True),
        dtype=np.float64,
    )


def logsumexp(values: np.ndarray) -> float:
    maximum = float(np.max(values))
    return maximum + math.log(float(np.exp(values - maximum).sum()))


def exactly_three(values: list[Any]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise ValueError("OOS detector must contain exactly three features")
    return float(values[0]), float(values[1]), float(values[2])


def is_multi_intent(text: str) -> bool:
    if not any(connector in text for connector in CONNECTORS):
        return False
    clauses = [
        clause.strip(" ，,。；;")
        for clause in re.split("|".join(map(re.escape, CONNECTORS)), text)
        if clause.strip(" ，,。；;")
    ]
    if len(clauses) < 2:
        return False
    signatures = [
        frozenset(
            index
            for index, group in enumerate(ACTION_GROUPS)
            if any(token in clause for token in group)
        )
        for clause in clauses
    ]
    actionful = [signature for signature in signatures if signature]
    return len(actionful) >= 2 and len(set(actionful)) >= 2


@dataclass(frozen=True)
class RuntimeMetadata:
    model_version: str
    labels: list[str]
    temperature: float
    auto_route_min_confidence: float
    clarify_below_confidence: float
    route_margin: float
    max_length: int
    oos_mean: tuple[float, float, float]
    oos_scale: tuple[float, float, float]
    oos_coefficients: tuple[float, float, float]
    oos_intercept: float
    oos_threshold: float


class IntentRuntime:
    def __init__(
        self,
        model_root: Path,
        route_map_path: Path = Path("configs/routing/intent_route_map.yaml"),
    ) -> None:
        self.model_root = model_root
        manifest_path = model_root / "model_manifest.json"
        onnx_path = model_root / "onnx" / "model.onnx"
        thresholds_path = model_root / "thresholds.yaml"
        required = [manifest_path, onnx_path, thresholds_path, model_root / "calibration.json"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Incomplete intent runtime package: {missing}")

        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        thresholds: dict[str, Any] = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
        calibration: dict[str, Any] = json.loads(
            (model_root / "calibration.json").read_text(encoding="utf-8")
        )
        route_config: dict[str, Any] = yaml.safe_load(route_map_path.read_text(encoding="utf-8"))
        labels = [str(value) for value in manifest["labels"]]
        expected_labels = [label.value for label in IntentLabel]
        if labels != expected_labels:
            raise ValueError("Published label order does not match the API contract")
        temperature = float(calibration["temperature"])
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("Calibration temperature must be finite and positive")
        oos_detector = calibration["oos_detector"]
        self.metadata = RuntimeMetadata(
            model_version=str(manifest["model_version"]),
            labels=labels,
            temperature=temperature,
            auto_route_min_confidence=float(thresholds["auto_route_min_confidence"]),
            clarify_below_confidence=float(thresholds["clarify_below_confidence"]),
            route_margin=float(thresholds["route_margin"]),
            max_length=int(manifest["max_length"]),
            oos_mean=exactly_three(oos_detector["mean"]),
            oos_scale=exactly_three(oos_detector["scale"]),
            oos_coefficients=exactly_three(oos_detector["coefficients"]),
            oos_intercept=float(oos_detector["intercept"]),
            oos_threshold=float(oos_detector["threshold"]),
        )
        self.route_map = {
            IntentLabel(label): HighLevelRoute(route)
            for label, route in route_config["routes"].items()
        }
        self.tokenizer = AutoTokenizer.from_pretrained(model_root, local_files_only=True)
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = max(1, int(os.getenv("ORT_INTRA_OP_THREADS", "2")))
        session_options.inter_op_num_threads = 1
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(onnx_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def warmup(self, iterations: int = 10) -> None:
        for _ in range(iterations):
            self.predict("查询订单物流进度")

    def _inputs(
        self, texts: list[str], previous_user_texts: list[str | None]
    ) -> dict[str, np.ndarray]:
        pairs = list(zip(texts, previous_user_texts, strict=True))
        first = [previous or text for text, previous in pairs]
        second = [text if previous else "" for text, previous in pairs]
        has_context = any(previous is not None for previous in previous_user_texts)
        encoded = self.tokenizer(
            first,
            text_pair=second if has_context else None,
            return_tensors="np",
            max_length=self.metadata.max_length,
            truncation=True,
            padding=True,
        )
        inputs: dict[str, np.ndarray] = {}
        for name in self.input_names:
            if name == "token_type_ids" and name not in encoded:
                inputs[name] = np.zeros_like(encoded["input_ids"], dtype=np.int64)
            else:
                inputs[name] = np.asarray(encoded[name], dtype=np.int64)
        return inputs

    def predict(self, text: str, previous_user_text: str | None = None) -> IntentPrediction:
        return self.predict_batch([text], [previous_user_text])[0]

    def _infer_batch(
        self,
        texts: list[str],
        previous_user_texts: list[str | None] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        if not texts:
            empty = np.empty((0, len(self.metadata.labels)), dtype=np.float64)
            return empty, empty
        contexts = previous_user_texts or [None] * len(texts)
        if len(contexts) != len(texts):
            raise ValueError("texts and previous_user_texts must have equal lengths")
        logits = np.asarray(
            self.session.run(["logits"], self._inputs(texts, contexts))[0],
            dtype=np.float64,
        )
        return logits, stable_softmax(logits, self.metadata.temperature)

    def predict_probabilities_batch(
        self,
        texts: list[str],
        previous_user_texts: list[str | None] | None = None,
    ) -> np.ndarray:
        """Return calibrated probabilities in ``metadata.labels`` order.

        Evaluation needs the full distribution for ECE and Brier score. Keeping
        this operation on the runtime prevents evaluation from reaching into the
        ONNX session or duplicating serving tokenization.
        """
        return self._infer_batch(texts, previous_user_texts)[1]

    def predict_batch(
        self,
        texts: list[str],
        previous_user_texts: list[str | None] | None = None,
    ) -> list[IntentPrediction]:
        if not texts:
            return []
        contexts = previous_user_texts or [None] * len(texts)
        logits, probabilities = self._infer_batch(texts, contexts)
        return [
            self._build_prediction(text, row_logits, row_probabilities)
            for text, row_logits, row_probabilities in zip(
                texts, logits, probabilities, strict=True
            )
        ]

    def _build_prediction(
        self, text: str, logits: np.ndarray, probabilities: np.ndarray
    ) -> IntentPrediction:
        ranked = np.argsort(probabilities)[::-1]
        model_label = IntentLabel(self.metadata.labels[int(ranked[0])])
        top_candidates = [
            IntentCandidate(
                label=IntentLabel(self.metadata.labels[int(index)]),
                probability=float(probabilities[int(index)]),
            )
            for index in ranked[:2]
        ]
        label = model_label
        source = "model"
        matched_rule: str | None = None
        normalized = re.sub(r"\s+", "", text)
        for rule_name, patterns, override in (
            ("explicit_human_handoff", HUMAN_PATTERNS, IntentLabel.HUMAN_HANDOFF),
            ("severe_complaint", COMPLAINT_PATTERNS, IntentLabel.COMPLAINT),
        ):
            if any(pattern in normalized for pattern in patterns):
                label = override
                source = "priority_rule"
                matched_rule = rule_name
                break

        confidence = float(probabilities[int(ranked[0])])
        margin = confidence - float(probabilities[int(ranked[1])])
        oos_probability = float(
            probabilities[self.metadata.labels.index(IntentLabel.OUT_OF_SCOPE.value)]
        )
        energy_score = -logsumexp(logits)
        raw_features = (oos_probability, confidence, energy_score)
        standardized = [
            (value - mean) / scale
            for value, mean, scale in zip(
                raw_features,
                self.metadata.oos_mean,
                self.metadata.oos_scale,
                strict=True,
            )
        ]
        oos_logit = self.metadata.oos_intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.metadata.oos_coefficients, standardized, strict=True)
        )
        oos_score = 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, oos_logit))))
        multi = is_multi_intent(text)
        decision: Literal["auto_route", "clarify", "safe_reply"]
        if source == "priority_rule":
            decision = "auto_route"
        elif multi:
            decision = "clarify"
            source = "multi_intent_detector"
        elif oos_score >= self.metadata.oos_threshold:
            label = IntentLabel.OUT_OF_SCOPE
            decision = "safe_reply"
            source = "oos_detector"
        elif (
            confidence >= self.metadata.auto_route_min_confidence
            and margin >= self.metadata.route_margin
        ):
            decision = "auto_route"
        elif confidence >= self.metadata.clarify_below_confidence:
            decision = "clarify"
            source = "confidence_gate"
        else:
            decision = "safe_reply"
            source = "confidence_gate"
        route = self.route_map[label]
        if decision == "safe_reply":
            route = HighLevelRoute.SAFE_REPLY
        return IntentPrediction(
            label=label,
            route=route,
            confidence=confidence,
            candidates=top_candidates,
            is_multi_intent=multi,
            model_version=self.metadata.model_version,
            route_source=source,
            decision=decision,
            margin=margin,
            oos_probability=oos_probability,
            oos_score=oos_score,
            energy_score=energy_score,
            model_label=model_label,
            matched_rule=matched_rule,
        )
