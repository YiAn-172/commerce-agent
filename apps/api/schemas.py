from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.approval_core.contracts import ApprovalDecision
from packages.contracts.after_sales import AfterSalesRequestType


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class SessionCreateRequest(ApiModel):
    session_id: str | None = Field(default=None, pattern=r"^ses_[A-Za-z0-9_-]{8,64}$")


class SessionResponse(ApiModel):
    session_id: str
    user_id: str
    state_version: int
    graph_version: str
    created_at: datetime
    updated_at: datetime


class MessageResponse(ApiModel):
    role: str
    content: str
    created_at: datetime


class SessionDetailResponse(SessionResponse):
    messages: list[MessageResponse]


class ChatRequest(ApiModel):
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{8,64}$")
    text: str = Field(min_length=1, max_length=4000)
    request_id: str | None = Field(default=None, pattern=r"^req_[A-Za-z0-9_-]{8,64}$")


class ChatResponse(ApiModel):
    session_id: str
    request_id: str
    trace_id: str
    status: str
    answer: str
    state_version: int
    route: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    products: list[dict[str, Any]] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    cache_status: Literal["HIT", "MISS", "BYPASS"] = Field(
        default="BYPASS",
        exclude=True,
    )


class AfterSalesConfirmRequest(ApiModel):
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]{8,64}$")
    order_id: str = Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")
    item_id: str = Field(pattern=r"^item_[A-Za-z0-9_-]{6,64}$")
    request_type: AfterSalesRequestType
    reason_code: str = Field(min_length=2, max_length=50)
    description: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")


class ApprovalDecisionBody(ApiModel):
    decision: ApprovalDecision
    state_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)


class KnowledgeDocumentRequest(ApiModel):
    document_type: Literal["faq", "policy", "activity", "product"]
    title: str = Field(min_length=2, max_length=240)
    source_uri: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)


class KnowledgeReindexRequest(ApiModel):
    document_ids: list[str] = Field(default_factory=list, max_length=1000)
    target_version: str = Field(pattern=r"^kb_[A-Za-z0-9_-]{8,40}$")
