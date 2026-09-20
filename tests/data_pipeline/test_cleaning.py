from packages.data_pipeline.cleaning import clean_text, detect_pii, normalized_text_hash


def test_cleaning_redacts_identity_and_order_tokens() -> None:
    result = clean_text("<b>联系我</b> 13800138000，订单 ORD-12345，a@example.com")

    assert result.text == "联系我 [手机号],订单 [订单号],[邮箱]"
    assert result.pii_status == "redacted"
    assert set(result.redactions) == {"mobile", "order_id", "email"}


def test_normalized_hash_ignores_spacing_and_punctuation() -> None:
    assert normalized_text_hash("订单 到哪了？") == normalized_text_hash("订单到哪了")


def test_address_value_is_redacted_but_address_question_is_retained() -> None:
    result = clean_text("收货地址：上海市浦东新区海棠路88号1栋")

    assert result.text == "地址:[地址]"
    assert "address" in result.redactions
    assert not detect_pii(result.text)
    assert clean_text("收货地址怎么修改？").text == "收货地址怎么修改?"
