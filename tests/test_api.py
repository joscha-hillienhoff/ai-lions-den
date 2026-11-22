from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend.config import load_config
from backend.jury.types import VerdictOutput
from backend.main import create_app
from backend.personas import INVESTOR_IDS
from backend.state import Round1Entry

from .helpers import COMPLETE_PITCH

PASSWORD = "test-password"


def _app() -> TestClient:
    return TestClient(
        create_app(
            load_config(
                {
                    "DEMO_MODE": "true",
                    "MODERATOR_PASSWORD": PASSWORD,
                    "SESSION_DB": ":memory:",
                }
            )
        )
    )


def _moderator(client: TestClient) -> TestClient:
    login = client.post("/api/moderator/login", json={"password": PASSWORD})
    assert login.status_code == 200
    return client


def _advance(moderator: TestClient, expected: str) -> dict:
    current = moderator.get("/api/moderator/state").json()["session"]["phase"]
    response = moderator.post("/api/moderator/phase", json={"expectedPhase": current})
    assert response.status_code == 200, response.text
    assert response.json()["session"]["phase"] == expected
    return response.json()


def _join(client: TestClient, name: str) -> dict:
    response = client.post("/api/teams/join", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def _pitch_payload() -> dict:
    return COMPLETE_PITCH.model_dump(by_alias=True)


def test_rejects_late_joining_after_ideation() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Early Team")

    _advance(moderator, "ideation")
    _join(TestClient(app.app), "Second Team")

    _advance(moderator, "jury")
    late = TestClient(app.app).post("/api/teams/join", json={"name": "Late Team"})
    assert late.status_code == 409
    assert late.json() == {"error": "Team registration is closed after the pitch phase."}


def test_rejects_duplicate_team_names() -> None:
    app = _app()
    _join(app, "The Margin Calls")
    duplicate = TestClient(app.app).post("/api/teams/join", json={"name": "  the margin   calls "})
    assert duplicate.status_code == 409
    assert duplicate.json() == {"error": "That team name is already taken."}


def test_accepts_twenty_teams_and_rejects_the_twenty_first() -> None:
    app = _app()
    for index in range(20):
        _join(TestClient(app.app), f"Team {index + 1:02d}")

    full = TestClient(app.app).post("/api/teams/join", json={"name": "Team 21"})
    assert full.status_code == 409
    assert full.json() == {"error": "The session is full (20 teams)."}

    moderator = _moderator(TestClient(app.app))
    state = moderator.get("/api/moderator/state").json()
    assert len(state["teams"]) == 20
    assert state["maxTeams"] == 20

    screen = TestClient(app.app).get("/api/screen/state").json()
    assert screen["counts"]["joined"] == 20
    assert screen["maxTeams"] == 20


async def test_rejects_verdict_retry_while_a_verdict_run_is_active() -> None:
    app = _app()
    team = _join(app, "Team")
    runtime = app.app.state.runtime
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowVerdictProvider:
        name = "demo"

        async def evaluate_round1(self, input_value):
            raise AssertionError("round 1 must not run during this verdict")

        async def evaluate_verdict(self, input_value):
            started.set()
            await release.wait()
            return VerdictOutput(statement="An independent verdict.", score=5)

    runtime.orchestrator._provider = SlowVerdictProvider()

    def prepare_verdict(state) -> None:
        team = state.teams[0]
        state.phase = "verdict"
        team.eligible = True
        for investor_id in INVESTOR_IDS:
            team.round1[investor_id] = Round1Entry(
                status="done",
                score=5,
                assessment="An independent assessment.",
                question="An independent question?",
            )

    app.app.state.runtime.store.update(prepare_verdict)

    async with AsyncClient(transport=ASGITransport(app=app.app), base_url="http://test") as client:
        login = await client.post("/api/moderator/login", json={"password": PASSWORD})
        assert login.status_code == 200
        team_id = team["team"]["id"]
        runtime.orchestrator.handle_phase_entered("verdict")
        await asyncio.wait_for(started.wait(), timeout=1)

        response = await client.post(
            "/api/moderator/retry",
            json={"teamId": team_id, "step": "verdict"},
        )
        assert response.status_code == 409
        assert response.json() == {"error": "A verdict run is already in progress."}

        release.set()
        while runtime.orchestrator.active_job_count:
            await asyncio.sleep(0)


def test_rejects_stale_pitch_revisions() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Team")
    _advance(moderator, "ideation")

    first = app.post(
        "/api/teams/pitch",
        json={
            "pitch": {"startupName": "Loopline"},
            "submit": False,
            "expectedPitchUpdatedAt": None,
        },
    )
    assert first.status_code == 200
    stale = app.post(
        "/api/teams/pitch",
        json={
            "pitch": {"startupName": "Loopline"},
            "submit": False,
            "expectedPitchUpdatedAt": None,
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {"error": "The pitch changed in another tab. Refresh before saving."}


def test_rejects_stale_defense_revisions_and_closed_phase() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Team")
    _advance(moderator, "ideation")
    saved = app.post(
        "/api/teams/pitch",
        json={
            "pitch": _pitch_payload(),
            "submit": True,
            "expectedPitchUpdatedAt": None,
        },
    )
    assert saved.status_code == 200
    _advance(moderator, "jury")
    _advance(moderator, "defense")

    answers = ["a", "b", "c", "d"]
    first = app.post(
        "/api/teams/defense",
        json={
            "answers": answers,
            "oneChange": "Narrow the first customer to independent pharmacies.",
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert first.status_code == 200
    submitted_at = first.json()["team"]["defense"]["submittedAt"]
    assert isinstance(submitted_at, int)

    stale = app.post(
        "/api/teams/defense",
        json={
            "answers": answers,
            "oneChange": "Narrow the first customer to independent pharmacies.",
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {"error": "The answers changed in another tab. Refresh before saving."}

    updated = app.post(
        "/api/teams/defense",
        json={
            "answers": answers,
            "oneChange": "Narrow the first customer to independent pharmacies.",
            "expectedDefenseSubmittedAt": submitted_at,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["team"]["defense"]["submittedAt"] > submitted_at

    _advance(moderator, "verdict")
    closed = app.post(
        "/api/teams/defense",
        json={
            "answers": answers,
            "oneChange": "Narrow the first customer to independent pharmacies.",
            "expectedDefenseSubmittedAt": updated.json()["team"]["defense"]["submittedAt"],
        },
    )
    assert closed.status_code == 409
    assert closed.json() == {"error": "The defense phase is closed."}


def test_rejects_incomplete_defense_answers_with_field_errors() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Team")
    _advance(moderator, "ideation")
    app.post(
        "/api/teams/pitch",
        json={"pitch": _pitch_payload(), "submit": True, "expectedPitchUpdatedAt": None},
    )
    _advance(moderator, "jury")
    _advance(moderator, "defense")

    response = app.post(
        "/api/teams/defense",
        json={
            "answers": ["a", "", "c", "d"],
            "oneChange": "Narrow the first customer to independent pharmacies.",
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert response.status_code == 400
    assert (
        response.json()["error"]
        == "Answer all four questions and describe your one change before submitting."
    )
    assert response.json()["fieldErrors"] == {"1": "An answer is required."}


def test_protects_moderator_routes_and_validates_finale_index() -> None:
    app = _app()
    anonymous = TestClient(app.app)
    assert anonymous.get("/api/moderator/state").status_code == 401
    assert (
        anonymous.post("/api/moderator/phase", json={"expectedPhase": "lobby"}).status_code == 401
    )

    moderator = _moderator(TestClient(app.app))
    invalid = moderator.post("/api/moderator/finale", json={"index": "1"})
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "Send an integer finalist index."}

    early = moderator.post("/api/moderator/finale", json={"index": 0})
    assert early.status_code == 409
    assert early.json() == {"error": "The finale spotlight is only available in the finale phase."}


@pytest.mark.parametrize("path", ["/api/team/state", "/api/screen/state", "/api/moderator/state"])
def test_all_views_publish_persona_names_focus_and_investor_types(path: str) -> None:
    client = _moderator(_app())
    response = client.get(path)
    assert response.status_code == 200
    investors = response.json()["investors"]
    assert [
        (entry["id"], entry["name"], entry["focus"], entry["title"], entry["investorType"])
        for entry in investors
    ] == [
        (
            "growth",
            "Victoria Lyon",
            "Market & Growth",
            "The Growth Hunter",
            "VC - Growth and scaling",
        ),
        (
            "finance",
            "Maximilian Löwenstein",
            "Money & Margins",
            "The Numbers Guy",
            "Private equity - Financial viability",
        ),
        (
            "customer",
            "Sophie Leone",
            "Customers & Demand",
            "The Market Realist",
            "Business angel - Customers and product-market fit",
        ),
        (
            "technology",
            "Dr. Alex Leander",
            "Tech & AI",
            "The AI Builder",
            "Tech investor - AI and defensibility",
        ),
    ]


def test_requires_stores_and_exposes_the_one_change_field() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Team")
    _advance(moderator, "ideation")
    app.post(
        "/api/teams/pitch",
        json={"pitch": _pitch_payload(), "submit": True, "expectedPitchUpdatedAt": None},
    )
    _advance(moderator, "jury")
    _advance(moderator, "defense")

    missing = app.post(
        "/api/teams/defense",
        json={
            "answers": ["a", "b", "c", "d"],
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert missing.status_code == 400
    assert "oneChange" in missing.json()["fieldErrors"]

    too_long = app.post(
        "/api/teams/defense",
        json={
            "answers": ["a", "b", "c", "d"],
            "oneChange": "x" * 1001,
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert too_long.status_code == 400
    assert "1000" in too_long.json()["fieldErrors"]["oneChange"]

    change = "We narrow the first customer to independent pharmacies and charge per store."
    saved = app.post(
        "/api/teams/defense",
        json={
            "answers": ["a", "b", "c", "d"],
            "oneChange": change,
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["team"]["defense"]["oneChange"] == change

    team_state = app.get("/api/team/state").json()
    assert team_state["team"]["defense"]["oneChange"] == change

    detail = moderator.get("/api/moderator/state").json()["teams"][0]
    assert detail["defense"]["oneChange"] == change
