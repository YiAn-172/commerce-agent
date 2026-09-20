from __future__ import annotations

from scripts.generate_ai_review_drafts import e2e_draft, rewrite_text


def test_ai_rewrite_templates_are_unique_for_required_counts() -> None:
    counts = {
        "product_search": 20,
        "product_recommend": 20,
        "product_compare": 20,
        "product_detail": 20,
        "stock_price": 20,
        "order_status": 20,
        "logistics_tracking": 20,
        "cancel_order": 20,
        "return_exchange": 20,
        "refund_progress": 66,
        "after_sales_eligibility": 20,
        "policy_faq": 20,
        "complaint": 20,
        "human_handoff": 20,
        "chitchat": 20,
        "out_of_scope": 20,
    }
    texts = [
        rewrite_text(label, index)
        for label, count in counts.items()
        for index in range(count)
    ]
    assert len(texts) == 366
    assert len(set(texts)) == 366


def test_e2e_ai_draft_accepts_fail_closed_search_fallback() -> None:
    row = {
        "scenario": "fail_search_products",
        "turns": [
            {"assistant_answer": "你好", "status": "completed"},
            {
                "assistant_answer": "暂时没有找到满足条件的商品。",
                "status": "insufficient_evidence",
            },
        ],
    }
    relevance, clarity, safe, verdict, rationale = e2e_draft(row)
    assert (relevance, clarity, safe, verdict) == (4, 4, True, "pass")
    assert "符合" in rationale
