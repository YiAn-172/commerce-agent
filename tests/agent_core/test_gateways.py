from __future__ import annotations

from packages.contracts.intent import HighLevelRoute, IntentLabel, IntentPrediction


def test_strict_intent_contract_parses_wire_json_enums() -> None:
    payload = """{
      "label":"product_detail",
      "route":"knowledge",
      "confidence":0.95,
      "candidates":[
        {"label":"product_detail","probability":0.95},
        {"label":"product_search","probability":0.03}
      ],
      "is_multi_intent":false,
      "model_version":"intent-v1",
      "route_source":"model",
      "decision":"auto_route",
      "margin":0.92,
      "oos_probability":0.01,
      "oos_score":0.01,
      "energy_score":-8.0,
      "model_label":"product_detail",
      "matched_rule":null
    }"""
    result = IntentPrediction.model_validate_json(payload)
    assert result.label is IntentLabel.PRODUCT_DETAIL
    assert result.route is HighLevelRoute.KNOWLEDGE
