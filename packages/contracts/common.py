from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import Field, model_validator

from packages.contracts.base import StrictModel

T = TypeVar("T")


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    STATE_CONFLICT = "STATE_CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ErrorDetail(StrictModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class ToolMeta(StrictModel):
    tool_call_id: str = Field(pattern=r"^tc_[A-Za-z0-9_-]{8,64}$")
    trace_id: str = Field(pattern=r"^tr_[A-Za-z0-9_-]{8,64}$")
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    as_of: datetime
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0, le=2)


class ToolEnvelope(StrictModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ErrorDetail | None = None
    meta: ToolMeta

    @model_validator(mode="after")
    def validate_result_shape(self) -> "ToolEnvelope[T]":
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("successful envelopes require data and forbid error")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("failed envelopes require error and forbid data")
        return self
