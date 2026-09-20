from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

from packages.contracts.base import StrictModel
from packages.contracts.intent import IntentPrediction
from packages.rag_core.models import RetrievalResult


class GraphLimits(StrictModel):
    max_nodes: int = Field(default=30, ge=1, le=100)
    max_tool_calls: int = Field(default=5, ge=1, le=20)
    max_llm_calls: int = Field(default=3, ge=1, le=10)
    max_repeated_tool_call: int = Field(default=2, ge=1, le=3)


class RewriteOutput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    doc_types: list[str] = Field(default_factory=list, max_length=4)


class KnowledgeAnswerOutput(StrictModel):
    answer: str = Field(min_length=1, max_length=3000)
    citation_ids: list[str] = Field(min_length=1, max_length=5)


class ShoppingSlotsOutput(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    brand: str | None = Field(default=None, max_length=60)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    region: str | None = Field(default=None, max_length=40)


class ShoppingAnswerOutput(StrictModel):
    answer: str = Field(min_length=1, max_length=3000)
    product_ids: list[str] = Field(min_length=1, max_length=10)
    citation_ids: list[str] = Field(min_length=1, max_length=5)


class AfterSalesSlotsOutput(StrictModel):
    order_id: str | None = Field(default=None, pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")
    item_id: str | None = Field(default=None, pattern=r"^item_[A-Za-z0-9_-]{6,64}$")
    request_type: Literal["return", "exchange", "repair", "cancel"] | None = None
    reason_code: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = Field(default=None, max_length=1000)


OutputT = TypeVar("OutputT", bound=BaseModel)


class IntentGateway(Protocol):
    async def predict(
        self, text: str, previous_user_text: str | None = None
    ) -> IntentPrediction: ...


class StructuredLLM(Protocol):
    async def generate(
        self,
        prompt_id: str,
        payload: dict[str, Any],
        response_model: type[OutputT],
    ) -> OutputT: ...


class RetrievalGateway(Protocol):
    async def retrieve(
        self,
        query: str,
        *,
        doc_types: list[str] | None = None,
        candidate_product_ids: set[str] | None = None,
    ) -> RetrievalResult: ...


class ToolGateway(Protocol):
    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
        trace_id: str,
        request_id: str,
        deadline_ms: int,
    ) -> dict[str, Any]: ...


class TraceSink(Protocol):
    async def persist(self, state: dict[str, Any]) -> None: ...
