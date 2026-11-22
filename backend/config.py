"""Application configuration, read from environment variables."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AppConfig:
    demo: bool
    api_key: str | None
    base_url: str | None
    model: str
    moderator_password: str
    moderator_password_configured: bool
    public_base_url: str | None
    temperature: float | None
    request_timeout_ms: float
    max_concurrency: int
    demo_delay_ms: float
    db_path: str
    # Countdown before the jury round advances to the defense by itself, in
    # milliseconds. 0 turns the automatic advance off.
    jury_auto_advance_ms: int = 15_000
    # Optional short link the projector shows instead of the long join link.
    # The QR code keeps encoding the real join URL.
    join_short_url: str | None = None


def _number_from_env(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        parsed = float(value)
    except ValueError:
        return fallback
    return parsed if math.isfinite(parsed) and parsed > 0 else fallback


def _auto_advance_ms_from_env(value: str | None) -> int:
    """Seconds from the environment as milliseconds; 0 disables, invalid values use 15 s."""
    if value is None or not value.strip():
        return 15_000
    try:
        parsed = float(value)
    except ValueError:
        return 15_000
    if not math.isfinite(parsed) or parsed < 0:
        return 15_000
    return round(parsed * 1000)


def _temperature_from_env(value: str | None) -> float | None:
    """An empty JURY_TEMPERATURE omits the parameter entirely."""
    if value is None:
        return 0.4
    if not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError:
        return 0.4
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 2 else 0.4


def _trimmed_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _http_url_or_none(value: str | None) -> str | None:
    """An http(s) URL with a host, trailing slash dropped; anything else is ignored."""
    trimmed = _trimmed_or_none(value)
    if trimmed is None:
        return None
    try:
        parts = urlsplit(trimmed)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and " " not in trimmed
    except ValueError:
        return None
    return trimmed.rstrip("/") if valid else None


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    values = environ if env is None else env
    api_key = _trimmed_or_none(values.get("OPENAI_API_KEY"))
    demo = values.get("DEMO_MODE") == "true" or not api_key

    configured_password = _trimmed_or_none(values.get("MODERATOR_PASSWORD"))
    moderator_password = configured_password
    if moderator_password is None:
        moderator_password = "" if values.get("NODE_ENV") == "production" else "moderator"

    base_url = _trimmed_or_none(values.get("OPENAI_BASE_URL"))
    public_base_url = _trimmed_or_none(values.get("PUBLIC_BASE_URL"))

    return AppConfig(
        demo=demo,
        api_key=api_key,
        base_url=base_url.rstrip("/") if base_url else None,
        model=_trimmed_or_none(values.get("OPENAI_MODEL")) or "gpt-4.1-mini",
        moderator_password=moderator_password,
        moderator_password_configured=configured_password is not None,
        public_base_url=public_base_url.rstrip("/") if public_base_url else None,
        temperature=_temperature_from_env(values.get("JURY_TEMPERATURE")),
        request_timeout_ms=_number_from_env(values.get("JURY_TIMEOUT_MS"), 60_000),
        max_concurrency=max(1, math.floor(_number_from_env(values.get("JURY_MAX_CONCURRENCY"), 8))),
        demo_delay_ms=_number_from_env(values.get("DEMO_DELAY_MS"), 2_200),
        db_path=_trimmed_or_none(values.get("SESSION_DB")) or "data/session.db",
        jury_auto_advance_ms=_auto_advance_ms_from_env(values.get("JURY_AUTO_ADVANCE_SECONDS")),
        join_short_url=_http_url_or_none(values.get("JOIN_SHORT_URL")),
    )
