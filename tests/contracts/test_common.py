from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.contracts.common import ErrorCode, ErrorDetail, ToolEnvelope, ToolMeta


def meta() -> ToolMeta:
    return ToolMeta(
        tool_call_id="tc_12345678",
        trace_id="tr_12345678",
        as_of=datetime.now(UTC),
        duration_ms=12,
    )


def test_success_envelope_requires_data() -> None:
    with pytest.raises(ValidationError):
        ToolEnvelope[str](ok=True, meta=meta())


def test_failure_envelope_requires_error() -> None:
    with pytest.raises(ValidationError):
        ToolEnvelope[str](ok=False, meta=meta())


def test_failure_envelope_is_valid() -> None:
    envelope = ToolEnvelope[str](
        ok=False,
        error=ErrorDetail(code=ErrorCode.NOT_FOUND, message="resource not found"),
        meta=meta(),
    )
    assert envelope.error is not None
    assert envelope.data is None
