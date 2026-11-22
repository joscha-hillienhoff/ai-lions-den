"""Moderator and team authentication helpers."""

from __future__ import annotations

import hashlib
import hmac

MODERATOR_COOKIE = "jury_moderator"
TEAM_COOKIE = "jury_team"
VOTER_COOKIE = "jury_voter"

MODERATOR_SCOPE = "ai-lions-den:moderator"


def moderator_token(password: str) -> str:
    return hmac.new(password.encode(), MODERATOR_SCOPE.encode(), hashlib.sha256).hexdigest()


def password_matches(value: str, password: str) -> bool:
    if not password:
        return False
    return hmac.compare_digest(value.encode(), password.encode())


def is_moderator_token(value: str | None, password: str) -> bool:
    if not value or not password:
        return False
    return hmac.compare_digest(moderator_token(password).encode(), value.encode())


def cookie_options(secure: bool, max_age_seconds: int | None = None) -> dict[str, object]:
    options: dict[str, object] = {
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "secure": secure,
    }
    if max_age_seconds is not None:
        options["max_age"] = max_age_seconds
    return options
