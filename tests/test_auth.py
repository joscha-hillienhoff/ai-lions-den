from backend.auth import (
    cookie_options,
    is_moderator_token,
    moderator_token,
    password_matches,
)


def test_derives_a_stable_token_only_from_the_configured_password() -> None:
    token = moderator_token("secret")
    assert len(token) == 64
    assert moderator_token("secret") == token
    assert is_moderator_token(token, "secret") is True
    assert is_moderator_token(token, "other") is False
    assert is_moderator_token("", "secret") is False
    assert is_moderator_token(None, "secret") is False


def test_checks_the_password_without_length_leaks() -> None:
    assert password_matches("secret", "secret") is True
    assert password_matches("secret", "secreu") is False
    assert password_matches("short", "a-much-longer-password") is False
    assert password_matches("", "") is False


def test_marks_cookies_http_only_and_optionally_secure() -> None:
    insecure = cookie_options(False, 60)
    assert insecure["httponly"] is True
    assert insecure["samesite"] == "lax"
    assert insecure["secure"] is False
    assert insecure["max_age"] == 60
    assert cookie_options(True)["secure"] is True
