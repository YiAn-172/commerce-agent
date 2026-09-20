from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    purpose: str
    input_schema: str
    output_schema: str
    allowed_facts: list[str]
    forbidden_actions: list[str]
    system: str


class PromptRegistry:
    def __init__(self, root: Path = Path("configs/prompts")) -> None:
        self.root = root

    def load(self, prompt_id: str) -> PromptSpec:
        if not prompt_id.replace("_", "").isalnum():
            raise ValueError("invalid prompt id")
        path = self.root / f"{prompt_id}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        required = {
            "prompt_id",
            "version",
            "purpose",
            "input_schema",
            "output_schema",
            "allowed_facts",
            "forbidden_actions",
            "system",
        }
        if not isinstance(raw, dict) or set(raw) != required or raw["prompt_id"] != prompt_id:
            raise ValueError(f"invalid prompt manifest: {path}")
        return PromptSpec(**raw)


class DeepSeekAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int = 1,
        temperature: float = 0.1,
        registry: PromptRegistry | None = None,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for live generation")
        if max_retries not in (0, 1):
            raise ValueError("online DeepSeek retries must be 0 or 1")
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self.registry = registry or PromptRegistry()
        self.client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def _request(self, messages: list[dict[str, str]]) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                create = cast(Any, self.client.chat.completions.create)
                response = await create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=self.temperature,
                    max_tokens=1200,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                return cast(str, response.choices[0].message.content or "{}")
            except (APIConnectionError, APITimeoutError, RateLimitError, APIStatusError) as error:
                retryable = isinstance(
                    error, (APIConnectionError, APITimeoutError, RateLimitError)
                ) or (isinstance(error, APIStatusError) and error.status_code >= 500)
                if not retryable or attempt == self.max_retries:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("unreachable DeepSeek retry state")

    async def generate(
        self,
        prompt_id: str,
        payload: dict[str, Any],
        response_model: type[OutputT],
    ) -> OutputT:
        prompt = self.registry.load(prompt_id)
        response_schema = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"{prompt.system}\n必须只输出 JSON 对象。"
                    f"输出字段定义：{prompt.output_schema}。"
                    f"输出必须严格匹配此 JSON Schema：{response_schema}。"
                    "不得添加 schema 未声明的字段。"
                    f"允许事实：{prompt.allowed_facts}。禁止行为：{prompt.forbidden_actions}。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = await self._request(messages)
        try:
            return response_model.model_validate_json(raw)
        except ValidationError as first_error:
            repair = [
                *messages,
                {"role": "assistant", "content": raw[:4000]},
                {
                    "role": "user",
                    "content": (
                        "上一个 JSON 不符合字段约束，请只修复 JSON。错误摘要："
                        + str(first_error)[:1000]
                        + f"。必须严格匹配 JSON Schema：{response_schema}，不得添加额外字段。"
                    ),
                },
            ]
            repaired = await self._request(repair)
            return response_model.model_validate_json(repaired)
