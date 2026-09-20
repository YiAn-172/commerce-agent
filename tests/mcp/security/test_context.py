import pytest

from packages.contracts.common import ErrorCode
from packages.mcp_core.context import context_from_headers
from packages.mcp_core.errors import ToolFailure


def headers() -> dict[str, str]:
    return {
        "X-Internal-Token": "test-secret",
        "X-Principal-Id": "usr_demo_0001",
        "X-Scopes": "catalog:read order:read",
        "X-Mcp-Audience": "commerce-mcp-order",
        "X-Trace-Id": "tr_test_12345678",
        "X-Request-Id": "req_test_12345678",
        "X-Deadline-Ms": "10000",
    }


def test_trusted_context_accepts_authenticated_gateway_headers() -> None:
    context = context_from_headers(
        headers(),
        expected_audience="commerce-mcp-order",
        internal_token="test-secret",
    )
    assert context.principal_id == "usr_demo_0001"
    assert context.scopes == frozenset({"catalog:read", "order:read"})


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("X-Internal-Token", "wrong", ErrorCode.PERMISSION_DENIED),
        ("X-Mcp-Audience", "commerce-mcp-catalog", ErrorCode.PERMISSION_DENIED),
        ("X-Principal-Id", "", ErrorCode.PERMISSION_DENIED),
        ("X-Deadline-Ms", "0", ErrorCode.INVALID_ARGUMENT),
        ("X-Trace-Id", "attacker", ErrorCode.INVALID_ARGUMENT),
    ],
)
def test_untrusted_context_is_rejected(field: str, value: str, code: ErrorCode) -> None:
    candidate = headers()
    candidate[field] = value
    with pytest.raises(ToolFailure) as caught:
        context_from_headers(
            candidate,
            expected_audience="commerce-mcp-order",
            internal_token="test-secret",
        )
    assert caught.value.code == code
