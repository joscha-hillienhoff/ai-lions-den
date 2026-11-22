from __future__ import annotations

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import backend.main as main
from backend.config import load_config
from backend.main import create_app

PASSWORD = "test-password"
FORWARDED = {"X-Forwarded-Proto": "https"}


class _ExplodingProvider:
    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"The health check must not use the jury provider ({name}).")


def _proxied(app) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(
        ProxyHeadersMiddleware(app, trusted_hosts="*"), base_url="http://internal:3000"
    )


def test_health_is_public_and_does_not_call_the_model() -> None:
    app = create_app(
        load_config(
            {
                "DEMO_MODE": "false",
                "OPENAI_API_KEY": "sk-not-used",
                "MODERATOR_PASSWORD": PASSWORD,
                "SESSION_DB": ":memory:",
            }
        )
    )
    app.state.runtime.provider = _ExplodingProvider()

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["demo"] is False
    assert body["teams"] == 0


def test_uses_the_forwarded_scheme_for_links_and_secure_cookies() -> None:
    app = create_app(
        load_config(
            {
                "DEMO_MODE": "true",
                "MODERATOR_PASSWORD": PASSWORD,
                "SESSION_DB": ":memory:",
            }
        )
    )
    client = _proxied(app)
    assert client.app is not None

    screen = client.get("/api/screen/state", headers=FORWARDED).json()
    assert screen["joinUrl"] == "https://internal:3000/team"
    assert screen["voteUrl"] == "https://internal:3000/vote"

    vote = client.get("/api/vote/state", headers=FORWARDED)
    assert vote.status_code == 200
    assert "secure" in vote.headers["set-cookie"].lower()

    plain = _proxied(app).get("/api/vote/state")
    assert "secure" not in plain.headers["set-cookie"].lower()


def test_public_base_url_still_drives_links_behind_the_proxy() -> None:
    app = create_app(
        load_config(
            {
                "DEMO_MODE": "true",
                "MODERATOR_PASSWORD": PASSWORD,
                "PUBLIC_BASE_URL": "https://lions.example.org/",
                "SESSION_DB": ":memory:",
            }
        )
    )

    screen = _proxied(app).get("/api/screen/state", headers=FORWARDED).json()

    assert screen["joinUrl"] == "https://lions.example.org/team"
    assert screen["voteUrl"] == "https://lions.example.org/vote"


def test_run_trusts_forwarded_headers_and_reads_the_port(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    monkeypatch.setenv("PORT", "8080")

    main.run()

    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 8080
    assert calls["proxy_headers"] is True
    assert calls["forwarded_allow_ips"] == "*"

    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    main.run()
    assert calls["forwarded_allow_ips"] == "10.0.0.0/8"


def test_join_short_url_is_shown_while_the_qr_keeps_the_real_join_url() -> None:
    from backend.qr import join_qr_data_url

    base = {
        "DEMO_MODE": "true",
        "MODERATOR_PASSWORD": PASSWORD,
        "PUBLIC_BASE_URL": "https://lions.example.org",
        "SESSION_DB": ":memory:",
    }
    with_short = (
        _proxied(create_app(load_config({**base, "JOIN_SHORT_URL": "https://short.example/join"})))
        .get("/api/screen/state", headers=FORWARDED)
        .json()
    )
    without = (
        _proxied(create_app(load_config(base))).get("/api/screen/state", headers=FORWARDED).json()
    )

    assert with_short["joinShortUrl"] == "https://short.example/join"
    assert with_short["joinUrl"] == "https://lions.example.org/team"
    assert with_short["qrDataUrl"] == join_qr_data_url("https://lions.example.org/team")
    assert without["joinShortUrl"] == ""
    assert without["qrDataUrl"] == with_short["qrDataUrl"]
