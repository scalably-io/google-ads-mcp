import pytest
from scalably_google_ads_mcp import server


def test_normalize_customer_id_accepts_plain_10_digits():
    assert server._normalize_customer_id("1234567890") == "1234567890"


def test_normalize_customer_id_accepts_hyphenated_and_strips_hyphens():
    assert server._normalize_customer_id("123-456-7890") == "1234567890"


def test_normalize_customer_id_accepts_int():
    assert server._normalize_customer_id(1234567890) == "1234567890"


def test_normalize_customer_id_rejects_wrong_length():
    with pytest.raises(ValueError):
        server._normalize_customer_id("12345")


def test_normalize_customer_id_rejects_non_numeric():
    with pytest.raises(ValueError):
        server._normalize_customer_id("abcdefghij")


def test_assert_read_only_gaql_accepts_plain_select():
    server._assert_read_only_gaql("SELECT campaign.id FROM campaign")


def test_assert_read_only_gaql_accepts_select_after_comment():
    server._assert_read_only_gaql("/* note */ SELECT campaign.id FROM campaign")


def test_assert_read_only_gaql_rejects_non_string():
    with pytest.raises(ValueError):
        server._assert_read_only_gaql(None)


def test_assert_read_only_gaql_rejects_empty_string():
    with pytest.raises(ValueError):
        server._assert_read_only_gaql("   ")


def test_assert_read_only_gaql_rejects_semicolon():
    with pytest.raises(ValueError):
        server._assert_read_only_gaql("SELECT campaign.id FROM campaign;")


def test_assert_read_only_gaql_rejects_non_select_start():
    with pytest.raises(ValueError):
        server._assert_read_only_gaql("DELETE FROM campaign")


def test_redact_hides_developer_token_client_secret_refresh_token_and_bearer():
    text = (
        '"developer_token": "abc123" '
        '"client_secret": "shh" '
        '"refresh_token": "1//xyz" '
        'Authorization: Bearer tok.en'
    )
    red = server._redact(text)
    assert "abc123" not in red
    assert "shh" not in red
    assert "1//xyz" not in red
    assert "tok.en" not in red


def test_fail_is_plain_runtime_error():
    with pytest.raises(RuntimeError, match=r"^google_ads_request_failed: boom "):
        server._fail("google_ads_query", "boom")


def test_no_private_envelope_in_source():
    import inspect
    src = inspect.getsource(server)
    assert "tool-outcome" + "/v1" not in src and "OUTCOME_SCHEMA" not in src
