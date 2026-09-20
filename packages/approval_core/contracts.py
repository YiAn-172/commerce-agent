from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from packages.contracts.base import StrictModel


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalDecisionRequest(StrictModel):
    decision: ApprovalDecision
    state_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)


class ApprovalResume(StrictModel):
    decision: ApprovalDecision
    state_version: int = Field(ge=1)
    decided_by: str = Field(pattern=r"^[A-Za-z0-9_-]{3,40}$")


class ApprovalSnapshot(StrictModel):
    approval_id: str
    ticket_id: str
    principal_id: str
    action_type: str
    status: str
    amount_snapshot: str | None = None
    state_version: int = Field(ge=1)
    session_id: str | None = None
    checkpoint_thread_id: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None
    resumed_at: datetime | None = None
    duplicate: bool = False


class RevalidationResult(StrictModel):
    valid: bool
    reason: str = Field(min_length=1, max_length=500)


class ApprovalWorkflowResult(StrictModel):
    approval_id: str
    ticket_id: str
    status: ApprovalDecision
    outcome: str
    revalidation_reason: str | None = None
    resumed_at: datetime
    duplicate: bool = False
