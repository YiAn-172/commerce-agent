from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.intent_service.api import app
from packages.contracts.intent import (
    HighLevelRoute,
    IntentCandidate,
    IntentLabel,
    IntentPrediction,
)


class FakeRuntime:
    model_root = Path("/fake/model")
    metadata = SimpleNamespace(model_version="fake-v1")

    def predict(self, text: str, previous_user_text: str | None = None) -> IntentPrediction:
        del text, previous_user_text
        return make_prediction()

    def predict_batch(
        self,
        texts: list[str],
        previous_user_texts: list[str | None] | None = None,
    ) -> list[IntentPrediction]:
        del previous_user_texts
        return [make_prediction() for _ in texts]


def make_prediction() -> IntentPrediction:
    return IntentPrediction(
        label=IntentLabel.LOGISTICS_TRACKING,
        route=HighLevelRoute.ORDER,
        confidence=0.96,
        candidates=[
            IntentCandidate(label=IntentLabel.LOGISTICS_TRACKING, probability=0.96),
            IntentCandidate(label=IntentLabel.ORDER_STATUS, probability=0.02),
        ],
        model_version="fake-v1",
        margin=0.94,
        oos_probability=0.001,
        energy_score=-8.0,
        model_label=IntentLabel.LOGISTICS_TRACKING,
    )


def test_not_ready_fails_closed() -> None:
    with TestClient(app) as client:
        app.state.intent_runtime = None
        app.state.load_error = "test model is unavailable"
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["message"] == "intent model is not ready"


def test_predict_and_batch_predict_use_loaded_runtime() -> None:
    with TestClient(app) as client:
        app.state.intent_runtime = FakeRuntime()
        app.state.load_error = None
        health = client.get("/health/ready")
        single = client.post("/predict", json={"text": "订单到哪了"})
        batch = client.post(
            "/batch_predict",
            json={"items": [{"text": "订单到哪了"}, {"text": "快递多久到"}]},
        )
    assert health.status_code == 200
    assert health.json()["model_version"] == "fake-v1"
    assert single.status_code == 200
    assert single.json()["label"] == "logistics_tracking"
    assert len(single.json()["candidates"]) == 2
    assert batch.status_code == 200
    assert len(batch.json()["predictions"]) == 2
