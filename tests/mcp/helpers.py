from packages.mcp_core.context import TrustedToolContext


def trusted_context(
    principal_id: str,
    *scopes: str,
    audience: str = "test-audience",
) -> TrustedToolContext:
    return TrustedToolContext(
        principal_id=principal_id,
        scopes=frozenset(scopes),
        audience=audience,
        trace_id="tr_test_12345678",
        request_id="req_test_12345678",
        deadline_ms=10_000,
    )
