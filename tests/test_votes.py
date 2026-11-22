from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.audience import jury_finalists
from backend.config import load_config
from backend.main import create_app
from backend.ranking import ranked_teams
from backend.store import Store

from .helpers import make_state, make_team, set_verdict, write_state

PASSWORD = "test-password"


def _teams():
    teams = []
    for index in (1, 2, 3, 4):
        team = make_team(index)
        set_verdict(
            team,
            {
                "growth": 10 - index,
                "finance": 10 - index,
                "customer": 10 - index,
                "technology": 10 - index,
            },
            {"growth": "in", "finance": "in", "customer": "in", "technology": "in"},
        )
        teams.append(team)
    return teams


def _client(phase: str = "voting"):
    app = create_app(
        load_config(
            {
                "DEMO_MODE": "true",
                "MODERATOR_PASSWORD": PASSWORD,
                "SESSION_DB": ":memory:",
            }
        )
    )
    runtime = app.state.runtime
    teams = _teams()
    state = make_state(teams, phase)
    state.leaderboard_team_ids = [entry.team.id for entry in ranked_teams(teams)]
    write_state(runtime.store, state)
    return TestClient(app), runtime, teams


def test_vote_state_creates_an_httponly_voter_cookie() -> None:
    client, _runtime, teams = _client()

    response = client.get("/api/vote/state")
    assert response.status_code == 200
    payload = response.json()
    assert payload["open"] is True
    assert payload["hasVoted"] is False
    assert len(payload["finalists"]) == 3
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.cookies.get("jury_voter")

    finalist_ids = {entry.team.id for entry in jury_finalists(make_state(teams, "voting"))}
    assert {entry["teamId"] for entry in payload["finalists"]} == finalist_ids


def test_vote_state_uses_the_pitch_order_without_exposing_jury_order() -> None:
    client, runtime, teams = _client()
    set_verdict(
        teams[0],
        {"growth": 7, "finance": 4, "customer": 4, "technology": 4},
        {"growth": "in", "finance": "out", "customer": "out", "technology": "out"},
    )
    set_verdict(
        teams[1],
        {"growth": 6, "finance": 6, "customer": 6, "technology": 6},
        {"growth": "out", "finance": "out", "customer": "out", "technology": "out"},
    )
    set_verdict(
        teams[2],
        {"growth": 8, "finance": 8, "customer": 2, "technology": 2},
        {"growth": "in", "finance": "in", "customer": "out", "technology": "out"},
    )
    set_verdict(
        teams[3],
        {"growth": 5, "finance": 5, "customer": 5, "technology": 5},
        {"growth": "out", "finance": "out", "customer": "out", "technology": "out"},
    )
    pitch_ids = [teams[1].id, teams[2].id, teams[0].id]
    state = make_state(teams, "voting", pitch_order_team_ids=pitch_ids)
    state.leaderboard_team_ids = [entry.team.id for entry in ranked_teams(teams)]
    write_state(runtime.store, state)

    payload = client.get("/api/vote/state").json()

    assert [entry["teamId"] for entry in payload["finalists"]] == pitch_ids
    assert all(
        "rank" not in entry and "total" not in entry and "inCount" not in entry
        for entry in payload["finalists"]
    )


def test_records_one_final_vote_per_browser() -> None:
    client, runtime, teams = _client()
    client.get("/api/vote/state")
    finalist_id = jury_finalists(make_state(teams, "voting"))[0].team.id

    first = client.post("/api/vote", json={"teamId": finalist_id})
    assert first.status_code == 200
    assert first.json() == {"ok": True, "votedTeamId": finalist_id}

    second = client.post("/api/vote", json={"teamId": finalist_id})
    assert second.status_code == 409
    assert second.json() == {"error": "You have already voted. Your vote is final."}

    assert runtime.store.vote_total() == 1
    assert client.get("/api/vote/state").json()["hasVoted"] is True


def test_rejects_votes_outside_the_voting_phase_and_for_non_finalists() -> None:
    early, _runtime, teams = _client("leaderboard")
    early.get("/api/vote/state")
    finalist_id = jury_finalists(make_state(teams, "voting"))[0].team.id
    not_open = early.post("/api/vote", json={"teamId": finalist_id})
    assert not_open.status_code == 409
    assert not_open.json() == {"error": "The audience vote has not opened yet."}

    late, _runtime, teams = _client("winner")
    late.get("/api/vote/state")
    finalist_id = jury_finalists(make_state(teams, "voting"))[0].team.id
    closed = late.post("/api/vote", json={"teamId": finalist_id})
    assert closed.status_code == 409
    assert closed.json() == {"error": "The audience vote is closed."}

    voting, _runtime, teams = _client()
    voting.get("/api/vote/state")
    non_finalist = next(
        team.id
        for team in teams
        if team.id not in {entry.team.id for entry in jury_finalists(make_state(teams, "voting"))}
    )
    rejected = voting.post("/api/vote", json={"teamId": non_finalist})
    assert rejected.status_code == 400
    assert rejected.json() == {"error": "That team is not in the finale."}


def test_advancing_to_the_winner_closes_voting() -> None:
    client, runtime, teams = _client("voting")
    moderator = TestClient(client.app)
    assert moderator.post("/api/moderator/login", json={"password": PASSWORD}).status_code == 200
    client.get("/api/vote/state")
    finalist_id = jury_finalists(make_state(teams, "voting"))[0].team.id

    advanced = moderator.post("/api/moderator/phase", json={"expectedPhase": "voting"})
    assert advanced.status_code == 200
    assert advanced.json()["session"]["phase"] == "winner"

    rejected = client.post("/api/vote", json={"teamId": finalist_id})
    assert rejected.status_code == 409
    assert rejected.json() == {"error": "The audience vote is closed."}
    assert runtime.store.vote_total() == 0


def test_concurrent_duplicates_count_once_and_many_voters_all_count() -> None:
    store = Store(":memory:")

    def same_voter(_index: int) -> str | None:
        return store.cast_vote("one-browser", "team-1", lambda _state: None)

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(same_voter, range(24)))

    assert outcomes.count(None) == 1
    assert outcomes.count("already-voted") == 23
    assert store.vote_counts() == {"team-1": 1}

    def voter(index: int) -> str | None:
        return store.cast_vote(f"browser-{index}", "team-1", lambda _state: None)

    with ThreadPoolExecutor(max_workers=24) as pool:
        many = list(pool.map(voter, range(60)))

    assert many.count(None) == 60
    assert store.vote_total() == 61
    store.close()


def test_votes_survive_a_restart_and_reset_clears_them() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "session.db")
        first = Store(path)
        assert first.cast_vote("browser-a", "team-1", lambda _state: None) is None
        assert first.cast_vote("browser-b", "team-2", lambda _state: None) is None
        first.close()

        second = Store(path)
        assert second.vote_counts() == {"team-1": 1, "team-2": 1}
        assert second.vote_for("browser-a") == "team-1"

        second.reset()
        assert second.vote_total() == 0
        assert second.vote_for("browser-a") is None
        second.close()
