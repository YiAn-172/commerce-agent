from packages.contracts.intent import IntentLabel


def test_intent_taxonomy_contains_exactly_sixteen_labels() -> None:
    assert len(IntentLabel) == 16
    assert len({label.value for label in IntentLabel}) == 16


def test_multi_intent_is_not_a_primary_label() -> None:
    assert "multi_intent" not in {label.value for label in IntentLabel}
