import pytest
from pydantic import ValidationError

from packages.contracts.after_sales import PrefillServiceTicketInput
from packages.contracts.order import GetOrderDetailInput, ListRecentOrdersInput


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (GetOrderDetailInput, {"order_id": "ord_123456", "user_id": "usr_attacker"}),
        (ListRecentOrdersInput, {"limit": 3, "principal_id": "usr_attacker"}),
        (
            PrefillServiceTicketInput,
            {
                "order_id": "ord_123456",
                "item_id": "item_123456",
                "request_type": "return",
                "reason_code": "damaged",
                "description": "received damaged item",
                "idempotency_key": "idem_123456789012",
                "user_id": "usr_attacker",
            },
        ),
    ],
)
def test_identity_fields_are_forbidden(model: object, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]


def test_recent_orders_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ListRecentOrdersInput(limit=100)
