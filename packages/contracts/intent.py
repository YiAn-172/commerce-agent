from enum import StrEnum
from typing import Literal

from pydantic import Field

from packages.contracts.base import StrictModel


class IntentLabel(StrEnum):
    PRODUCT_SEARCH = "product_search"
    PRODUCT_RECOMMEND = "product_recommend"
    PRODUCT_COMPARE = "product_compare"
    PRODUCT_DETAIL = "product_detail"
    STOCK_PRICE = "stock_price"
    ORDER_STATUS = "order_status"
    LOGISTICS_TRACKING = "logistics_tracking"
    CANCEL_ORDER = "cancel_order"
    RETURN_EXCHANGE = "return_exchange"
    REFUND_PROGRESS = "refund_progress"
    AFTER_SALES_ELIGIBILITY = "after_sales_eligibility"
    POLICY_FAQ = "policy_faq"
    COMPLAINT = "complaint"
    HUMAN_HANDOFF = "human_handoff"
    CHITCHAT = "chitchat"
    OUT_OF_SCOPE = "out_of_scope"


class HighLevelRoute(StrEnum):
    KNOWLEDGE = "knowledge"
    SHOPPING = "shopping"
    ORDER = "order"
    AFTER_SALES = "after_sales"
    HUMAN = "human"
    GENERAL = "general"
    SAFE_REPLY = "safe_reply"


class IntentCandidate(StrictModel):
    label: IntentLabel
    probability: float = Field(ge=0.0, le=1.0)


class IntentPrediction(StrictModel):
    label: IntentLabel
    route: HighLevelRoute
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[IntentCandidate] = Field(min_length=2, max_length=2)
    is_multi_intent: bool = False
    model_version: str = Field(min_length=1, max_length=100)
    route_source: str = Field(default="model", max_length=50)
    decision: Literal["auto_route", "clarify", "safe_reply"] = "auto_route"
    margin: float = Field(default=0.0, ge=0.0, le=1.0)
    oos_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    oos_score: float = Field(default=0.0, ge=0.0, le=1.0)
    energy_score: float = 0.0
    model_label: IntentLabel | None = None
    matched_rule: str | None = Field(default=None, max_length=100)
