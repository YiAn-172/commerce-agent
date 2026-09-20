from __future__ import annotations

from scripts.finalize_intent_challenge import parse_intents
from scripts.human_review_workspace import gold_status, parse_tools, valid_timestamp


def test_review_workspace_parses_tools_and_timestamps() -> None:
    assert parse_tools('["search_products", "check_inventory"]') == [
        "search_products",
        "check_inventory",
    ]
    assert parse_tools("search_products, check_inventory") == [
        "search_products",
        "check_inventory",
    ]
    assert valid_timestamp("2026-09-18T18:30:00+08:00") is True
    assert valid_timestamp("2026-09-18T18:30:00") is False


def test_gold_status_requires_audited_independent_reviews() -> None:
    row = {
        "double_annotation_required": "True",
        "annotator_1_text": "查询订单状态",
        "annotator_1_label": "order_status",
        "annotator_1_id": "ann_a",
        "annotator_1_reviewed_at": "2026-09-18T18:30:00+08:00",
        "annotator_2_text": "查询订单状态",
        "annotator_2_label": "order_status",
        "annotator_2_id": "ann_b",
        "annotator_2_reviewed_at": "2026-09-18T18:31:00+08:00",
        "adjudicated_text": "查询订单状态",
        "adjudicated_label": "order_status",
        "adjudicator_id": "lead",
        "adjudicated_at": "2026-09-18T18:32:00+08:00",
        "status": "adjudicated",
    }
    assert gold_status([row]) == {
        "annotator_1_complete": 1,
        "annotator_1_total": 1,
        "annotator_2_complete": 1,
        "annotator_2_total": 1,
        "adjudicated": 1,
        "adjudication_total": 1,
    }


def test_challenge_intents_are_bounded_and_known() -> None:
    assert parse_intents('["cancel_order", "product_search"]') == [
        "cancel_order",
        "product_search",
    ]
    assert parse_intents("[]") == []
