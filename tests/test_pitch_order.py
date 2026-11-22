"""The finalists' random, fixed pitch order."""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import MAX_TEAMS
from backend.audience import jury_finalists, pitch_ordered_finalists
from backend.config import load_config
from backend.main import create_app
from backend.phase_transition import advance_phase, retreat_phase
from backend.state import SessionState
from backend.store import Store
from backend.views import (
    moderator_state_view,
    screen_state_view,
    team_state_view,
    vote_state_view,
)

from .helpers import make_config, make_state, make_team, set_verdict, write_state

PASSWORD = "test-password"
# With this seed the shuffle of the three finalists differs from the jury order
# and from the registration order, so a ranking-derived order would fail the tests.
SEED = 4


def _teams():
    teams = []
    for index in (1, 2, 3, 4):
        team = make_team(index)
        score = 10 - index
        set_verdict(
            team,
            {"growth": score, "finance": score, "customer": score, "technology": score},
            {"growth": "in", "finance": "in", "customer": "in", "technology": "in"},
        )
        teams.append(team)
    return teams


def _leaderboard_state(seed: int = SEED) -> SessionState:
    state = make_state(_teams(), "verdict")
    outcome = advance_phase(state, rng=random.Random(seed))
    assert outcome.ok is True and outcome.phase == "leaderboard"
    return state


def _jury_ids(state: SessionState) -> list[str]:
    return [entry.team.id for entry in jury_finalists(state)]


def _views(state: SessionState) -> dict[str, list[str]]:
    teams = state.teams
    screen = screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        vote_url="http://localhost:3000/vote",
        vote_qr_data_url="data:image/png;base64,vote",
        max_teams=MAX_TEAMS,
    )
    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    return {
        "screen": [entry["teamId"] for entry in screen["finalists"]],
        "team": [entry["teamId"] for entry in team_state_view(state, teams[0])["finalists"]],
        "vote": [
            entry["teamId"] for entry in vote_state_view(state, voted_team_id=None)["finalists"]
        ],
        "moderator": [entry["teamId"] for entry in moderator["finale"]["finalists"]],
    }


def test_the_pitch_order_is_a_permutation_of_the_finalists() -> None:
    for seed in range(20):
        state = _leaderboard_state(seed)
        assert state.pitch_order_team_ids is not None
        assert sorted(state.pitch_order_team_ids) == sorted(_jury_ids(state))
        assert len(set(state.pitch_order_team_ids)) == 3


def test_the_pitch_order_is_drawn_without_looking_at_the_ranking() -> None:
    state = _leaderboard_state()
    jury_ids = _jury_ids(state)
    registration_ids = [team.id for team in state.teams if team.id in jury_ids]

    assert state.pitch_order_team_ids is not None
    assert state.pitch_order_team_ids != jury_ids
    assert state.pitch_order_team_ids != registration_ids
    # Across many draws every finalist pitches first sometimes, not only the top ranked.
    first_pitchers = {
        _leaderboard_state(seed).pitch_order_team_ids[0]  # type: ignore[index]
        for seed in range(40)
    }
    assert first_pitchers == set(jury_ids)


def test_the_pitch_order_is_drawn_once_and_survives_later_phases() -> None:
    state = _leaderboard_state()
    drawn = list(state.pitch_order_team_ids or [])

    for phase in ("finale", "voting", "winner"):
        outcome = advance_phase(state, rng=random.Random(SEED + 1))
        assert outcome.ok is True and outcome.phase == phase
        assert state.pitch_order_team_ids == drawn
        if phase != "winner":
            assert [entry.team.id for entry in pitch_ordered_finalists(state)] == drawn


def test_every_view_lists_the_same_pitch_order_on_every_read() -> None:
    state = _leaderboard_state()
    drawn = list(state.pitch_order_team_ids or [])

    for phase in ("leaderboard", "finale", "voting"):
        state.phase = phase  # type: ignore[assignment]
        for _ in range(3):
            views = _views(state)
            assert views == {name: drawn for name in ("screen", "team", "vote", "moderator")}


def test_the_moderator_spotlight_follows_the_pitch_order_and_keeps_the_figures() -> None:
    state = _leaderboard_state()
    state.phase = "finale"
    jury_ids = _jury_ids(state)

    finale = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)["finale"]
    assert [entry["teamId"] for entry in finale["finalists"]] == state.pitch_order_team_ids
    assert [entry["rank"] for entry in finale["finalists"]] == [
        jury_ids.index(entry["teamId"]) + 1 for entry in finale["finalists"]
    ]


def test_a_session_without_a_stored_order_falls_back_to_registration_order() -> None:
    teams = _teams()
    state = make_state(teams, "leaderboard")
    state.leaderboard_team_ids = [team.id for team in teams]
    assert state.pitch_order_team_ids is None

    assert [entry.team.id for entry in pitch_ordered_finalists(state)] == [
        team.id for team in teams[:3]
    ]


def test_a_stored_order_that_does_not_match_the_finalists_is_ignored() -> None:
    state = _leaderboard_state()
    state.pitch_order_team_ids = [*(state.pitch_order_team_ids or [])[:2], "team-4"]

    registration_ids = [team.id for team in state.teams if team.id in _jury_ids(state)]
    assert [entry.team.id for entry in pitch_ordered_finalists(state)] == registration_ids


def test_the_pitch_order_survives_a_reload_of_the_persisted_state() -> None:
    state = _leaderboard_state()
    drawn = list(state.pitch_order_team_ids or [])
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "session.sqlite")
        first = Store(path)
        write_state(first, state)
        before = _views(first.read())
        first.close()

        second = Store(path)
        reloaded = second.read()
        assert reloaded.pitch_order_team_ids == drawn
        assert _views(reloaded) == before == {name: drawn for name in before}
        second.close()


def test_older_stored_sessions_without_a_pitch_order_still_load() -> None:
    store = Store(":memory:")
    store.update(lambda state: setattr(state, "pitch_order_team_ids", None))
    assert store.read().pitch_order_team_ids is None


def test_the_api_draws_the_order_once_and_keeps_it_through_reads_and_a_restart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "session.sqlite")

        def make_app():
            return create_app(
                load_config(
                    {
                        "DEMO_MODE": "true",
                        "MODERATOR_PASSWORD": PASSWORD,
                        "SESSION_DB": path,
                    }
                )
            )

        app = make_app()
        with TestClient(app) as client:
            teams = _teams()
            write_state(app.state.runtime.store, make_state(teams, "verdict"))
            client.post("/api/moderator/login", json={"password": PASSWORD})
            response = client.post("/api/moderator/phase", json={"expectedPhase": "verdict"})
            assert response.status_code == 200, response.text

            def order() -> dict[str, list[str]]:
                moderator = client.get("/api/moderator/state").json()
                return {
                    "screen": [
                        e["teamId"] for e in client.get("/api/screen/state").json()["finalists"]
                    ],
                    "vote": [
                        e["teamId"] for e in client.get("/api/vote/state").json()["finalists"]
                    ],
                    "moderator": [e["teamId"] for e in moderator["finale"]["finalists"]],
                }

            first = order()
            assert sorted(first["screen"]) == sorted(_jury_ids(make_state(teams, "leaderboard")))
            assert first["screen"] == first["vote"] == first["moderator"]
            assert order() == first
            drawn = app.state.runtime.store.read().pitch_order_team_ids
            assert drawn == first["screen"]

            for expected_phase in ("leaderboard", "finale"):
                moved = client.post("/api/moderator/phase", json={"expectedPhase": expected_phase})
                assert moved.status_code == 200, moved.text
                assert order() == first

        restarted = make_app()
        with TestClient(restarted) as client:
            assert [
                e["teamId"] for e in client.get("/api/screen/state").json()["finalists"]
            ] == first["screen"]


def test_stepping_back_and_advancing_again_keeps_the_drawn_pitch_order() -> None:
    state = _leaderboard_state()
    drawn = list(state.pitch_order_team_ids or [])

    assert advance_phase(state, rng=random.Random(SEED + 1)).phase == "finale"
    assert retreat_phase(state).phase == "leaderboard"
    assert retreat_phase(state).phase == "verdict"
    assert advance_phase(state, rng=random.Random(SEED + 2)).phase == "leaderboard"

    assert state.pitch_order_team_ids == drawn
