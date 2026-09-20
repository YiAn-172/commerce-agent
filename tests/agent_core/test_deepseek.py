from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from packages.agent_core.contracts import RewriteOutput
from packages.agent_core.deepseek import DeepSeekAdapter


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def fake_client(completions: FakeCompletions) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


@pytest.mark.asyncio
async def test_deepseek_repairs_invalid_json_once_and_disables_thinking() -> None:
    completions = FakeCompletions(
        [
            '{"query":"退货政策","unexpected":true}',
            '{"query":"退货政策","doc_types":["policy"]}',
        ]
    )
    adapter = DeepSeekAdapter(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        timeout_seconds=5,
        max_retries=0,
        client=fake_client(completions),
    )
    result = await adapter.generate(
        "knowledge_rewrite_v1", {"query": "退货政策"}, RewriteOutput
    )
    assert result.doc_types == ["policy"]
    assert len(completions.calls) == 2
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "不得添加 schema 未声明的字段" in completions.calls[0]["messages"][0]["content"]
    assert '"additionalProperties":false' in completions.calls[0]["messages"][0]["content"]
    assert "错误摘要" in completions.calls[1]["messages"][-1]["content"]
    assert "不得添加额外字段" in completions.calls[1]["messages"][-1]["content"]


def test_deepseek_requires_key_and_rejects_more_than_one_retry() -> None:
    with pytest.raises(ValueError, match="API_KEY"):
        DeepSeekAdapter(
            api_key="",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            timeout_seconds=5,
        )
    with pytest.raises(ValueError, match="0 or 1"):
        DeepSeekAdapter(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            timeout_seconds=5,
            max_retries=2,
        )
