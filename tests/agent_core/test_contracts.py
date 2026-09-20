from __future__ import annotations

import pytest

from packages.agent_core.budget import BudgetExceeded, consume_tool
from packages.agent_core.contracts import GraphLimits
from packages.agent_core.deepseek import PromptRegistry
from packages.agent_core.persistence import redact_message
from packages.agent_core.state import CommerceState


def test_all_prompt_manifests_are_strict_and_loadable() -> None:
    registry = PromptRegistry()
    for prompt_id in (
        "knowledge_rewrite_v1",
        "knowledge_answer_v1",
        "shopping_slots_v1",
        "shopping_answer_v1",
        "after_sales_slots_v1",
    ):
        prompt = registry.load(prompt_id)
        assert prompt.prompt_id == prompt_id
        assert prompt.version == "1.0"
        assert prompt.forbidden_actions


def test_repeated_tool_call_budget_uses_canonical_arguments() -> None:
    state = CommerceState(tool_call_count=0, tool_call_counts={})
    limits = GraphLimits(max_repeated_tool_call=2)
    total, counts, fingerprint = consume_tool(
        state, limits, "search_products", {"query": "耳机", "limit": 5}
    )
    state["tool_call_count"] = total
    state["tool_call_counts"] = counts
    total, counts, repeated = consume_tool(
        state, limits, "search_products", {"limit": 5, "query": "耳机"}
    )
    assert repeated == fingerprint
    state["tool_call_count"] = total
    state["tool_call_counts"] = counts
    with pytest.raises(BudgetExceeded, match="repeated tool call"):
        consume_tool(state, limits, "search_products", {"query": "耳机", "limit": 5})


def test_trace_redaction_removes_phone_and_email() -> None:
    value = redact_message("联系 13800138000 或 user@example.com")
    assert value == "联系 [PHONE] 或 [EMAIL]"
