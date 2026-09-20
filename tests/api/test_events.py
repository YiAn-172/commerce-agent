from datetime import UTC, datetime

from packages.api_core.events import SseEvent, encode_sse, sanitize_payload


def test_sse_payload_removes_secrets_and_hidden_reasoning_recursively() -> None:
    sanitized = sanitize_payload(
        {
            "answer": "可以办理",
            "token": "secret-token",
            "nested": {"reasoning_content": "hidden", "status": "ok"},
        }
    )
    assert sanitized == {"answer": "可以办理", "nested": {"status": "ok"}}


def test_sse_wire_format_contains_id_type_and_single_json_data_record() -> None:
    event = SseEvent(
        event_id="evt_ses_demo_00000001",
        session_id="ses_demo_00000001",
        run_id="run_demo_00000001",
        sequence=1,
        timestamp=datetime.now(UTC),
        type="completed",
        payload={"status": "completed"},
    )
    encoded = encode_sse(event)
    assert encoded.startswith("id: evt_ses_demo_00000001\nevent: completed\ndata: ")
    assert encoded.endswith("\n\n")
    assert "reasoning" not in encoded
