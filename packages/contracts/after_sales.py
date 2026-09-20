from datetime import datetime
from enum import StrEnum

from pydantic import Field

from packages.contracts.base import StrictModel
from packages.contracts.common import RiskLevel


class AfterSalesRequestType(StrEnum):
    RETURN = "return"
    EXCHANGE = "exchange"
    REPAIR = "repair"
    CANCEL = "cancel"


class AfterSalesEligibilityInput(StrictModel):
    order_id: str = Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")
    item_id: str = Field(pattern=r"^item_[A-Za-z0-9_-]{6,64}$")
    request_type: AfterSalesRequestType
    reason_code: str = Field(min_length=2, max_length=50)


class PrefillServiceTicketInput(AfterSalesEligibilityInput):
    description: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")


class AfterSalesEligibilityResult(StrictModel):
    eligible: bool
    rule_code: str
    rule_version: str
    deadline: datetime | None = None
    required_materials: list[str] = Field(default_factory=list)
    reason_code: str
    reason: str
    risk_level: RiskLevel
    requires_human_approval: bool


class ServiceTicketDraft(StrictModel):
    ticket_id: str
    approval_id: str
    order_id: str
    item_id: str
    risk_level: RiskLevel
    approval_status: str
    approval_state_version: int = Field(ge=1)
    approval_expires_at: datetime | None = None
    refund_amount_snapshot: str | None = None
    state_version: int = Field(ge=1)
    created_at: datetime
    duplicate: bool = False
