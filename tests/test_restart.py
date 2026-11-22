"""Server restarts mid-session.

Each generation runs its own app and TestClient portal against the same SQLite
file. Leaving the client context cancels background work, which is the same
observable state a killed process leaves behind: a step marked running in the
database with nobody working on it.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.personas import INVESTOR_IDS

from .helpers import COMPLETE_PITCH, TEST_MODERATOR_PASSWORD, demo_app

PASSWORD = TEST_MODERATOR_PASSWORD


def _pitch(index: int) -> dict[str, str]:
    return COMPLETE_PITCH.model_dump(by_alias=True) | {"startupName": f"Startup {index}"}


SPECIAL_PITCH = _pitch(1) | {
    "startupName": "Löwenline 🦁",
    "tagline": "Rückgabe in einem Klick - Ärger adé 🚀",
    "solution": "Ein Klick macht aus einer Retoure einen lokalen Wiederverkauf. Grüße.",
}


def _defense(index: int) -> dict[str, Any]:
    return {
        "answers": [
            f"Team {index} answer one with äöü and 🚀.",
            f"Team {index} answer two with a price.",
            f"Team {index} answer three with a pilot result.",
            f"Team {index} answer four with the next step.",
        ],
        "oneChange": f"Team {index} ändert den ersten Kundensegment-Fokus. ✅",
    }


def _login(client: TestClient) -> None:
    response = client.post("/api/moderator/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text


def _join(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/api/teams/join", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def _advance(client: TestClient, expected: str) -> None:
    current = client.get("/api/moderator/state").json()["session"]["phase"]
    response = client.post("/api/moderator/phase", json={"expectedPhase": current})
    assert response.status_code == 200, response.text
    assert response.json()["session"]["phase"] == expected


def _moderator_state(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/moderator/state")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for(predicate: Callable[[], bool], timeout_ms: float = 15_000) -> None:
    deadline = time.time() * 1000 + timeout_ms
    while time.time() * 1000 < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for the server state.")


class _BlockingProvider:
    name = "demo"

    async def evaluate_round1(self, input_value: Any) -> Any:
        await asyncio.Event().wait()

    async def evaluate_verdict(self, input_value: Any) -> Any:
        await asyncio.Event().wait()


def test_a_restart_mid_jury_recovers_a_step_left_running() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-restart-") as directory:
        path = str(Path(directory) / "session.db")

        first = demo_app(SESSION_DB=path)
        with TestClient(first) as moderator:
            _login(moderator)
            team = _join(moderator, "Löwen & Söhne 🦁")
            _advance(moderator, "ideation")
            saved = moderator.post(
                "/api/teams/pitch",
                json={
                    "pitch": SPECIAL_PITCH,
                    "submit": True,
                    "expectedPitchUpdatedAt": None,
                },
            )
            assert saved.status_code == 200, saved.text

            # The provider blocks forever, so the restart leaves round 1 running.
            first.state.runtime.orchestrator._provider = _BlockingProvider()
            _advance(moderator, "jury")

            _wait_for(
                lambda: any(
                    entry["status"] == "running"
                    for entry in _moderator_state(moderator)["teams"][0]["round1"].values()
                ),
                timeout_ms=5_000,
            )
            team_id = team["team"]["id"]
            cookies = dict(moderator.cookies)

        second = demo_app(SESSION_DB=path)
        with TestClient(second, cookies=cookies) as moderator:
            _login(moderator)
            recovered = _moderator_state(moderator)
            assert recovered["session"]["phase"] == "jury"
            assert len(recovered["teams"]) == 1
            assert recovered["teams"][0]["id"] == team_id
            assert recovered["teams"][0]["pitch"] == SPECIAL_PITCH

            _wait_for(
                lambda: all(
                    all(entry["status"] == "done" for entry in team_detail["round1"].values())
                    for team_detail in _moderator_state(moderator)["teams"]
                )
            )
            state = _moderator_state(moderator)
            assert state["teams"][0]["eligible"] is True
            for investor_id in INVESTOR_IDS:
                assert state["teams"][0]["round1"][investor_id]["status"] == "done"
                assert state["teams"][0]["round1"][investor_id]["score"] is not None


def test_an_unsubmitted_pitch_draft_survives_a_restart() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-restart-") as directory:
        path = str(Path(directory) / "session.db")
        draft = _pitch(1) | {"tagline": "A tagline that is still being drafted"}

        first = demo_app(SESSION_DB=path)
        with TestClient(first) as moderator:
            _login(moderator)
            team = _join(moderator, "Löwen & Söhne 🦁")
            _advance(moderator, "ideation")
            saved = moderator.post(
                "/api/teams/pitch",
                json={
                    "pitch": draft,
                    "submit": False,
                    "expectedPitchUpdatedAt": None,
                },
            )
            assert saved.status_code == 200, saved.text
            cookies = dict(moderator.cookies)

        second = demo_app(SESSION_DB=path)
        with TestClient(second, cookies=cookies) as moderator:
            _login(moderator)
            view = moderator.get("/api/team/state").json()["team"]
            assert view["id"] == team["team"]["id"]
            assert view["pitch"] == draft
            assert view["submittedAt"] is None

            # The draft is still editable, and submitting it now locks it in.
            saved = moderator.post(
                "/api/teams/pitch",
                json={
                    "pitch": _pitch(1),
                    "submit": True,
                    "expectedPitchUpdatedAt": view["pitchUpdatedAt"],
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["team"]["submittedAt"] is not None


def test_state_survives_restarts_across_phases_votes_and_the_reveal() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-restart-") as directory:
        path = str(Path(directory) / "session.db")
        names = ["Löwen & Söhne 🦁", "Team Beta", "Team Gamma"]
        pitches = [SPECIAL_PITCH, _pitch(2), _pitch(3)]
        team_cookies: list[dict[str, str]] = []
        team_ids: list[str] = []

        # Generation 1: register, pitch, and finish jury round 1.
        first = demo_app(SESSION_DB=path)
        team_clients = [TestClient(first) for _ in names]
        with TestClient(first) as moderator:
            _login(moderator)
            for client, name in zip(team_clients, names, strict=True):
                team_ids.append(_join(client, name)["team"]["id"])
            _advance(moderator, "ideation")
            for client, pitch in zip(team_clients, pitches, strict=True):
                saved = client.post(
                    "/api/teams/pitch",
                    json={
                        "pitch": pitch,
                        "submit": True,
                        "expectedPitchUpdatedAt": None,
                    },
                )
                assert saved.status_code == 200, saved.text
                team_cookies.append(dict(client.cookies))
            _advance(moderator, "jury")
            _wait_for(
                lambda: all(
                    all(entry["status"] == "done" for entry in team_detail["round1"].values())
                    for team_detail in _moderator_state(moderator)["teams"]
                )
            )

        # Generation 2: the pitches and round-1 results are still there; the
        # defense is submitted and the final verdict completes.
        second = demo_app(SESSION_DB=path)
        with TestClient(second) as moderator:
            _login(moderator)
            state = _moderator_state(moderator)
            assert state["session"]["phase"] == "jury"
            assert [detail["id"] for detail in state["teams"]] == team_ids
            assert [detail["pitch"] for detail in state["teams"]] == pitches
            for detail in state["teams"]:
                assert detail["round1"]["growth"]["score"] is not None

            _advance(moderator, "defense")
            for index, cookies in enumerate(team_cookies):
                client = TestClient(second, cookies=cookies)
                saved_answers = client.post(
                    "/api/teams/defense",
                    json={**_defense(index + 1), "expectedDefenseSubmittedAt": None},
                )
                assert saved_answers.status_code == 200, saved_answers.text

            _advance(moderator, "verdict")
            _wait_for(
                lambda: all(
                    team_detail["verdictCompletedAt"] is not None
                    for team_detail in _moderator_state(moderator)["teams"]
                )
            )

        # Generation 3: verdicts and defense answers survive; run the public vote.
        phone_cookies: list[dict[str, str]] = []
        third = demo_app(SESSION_DB=path)
        with TestClient(third) as moderator:
            _login(moderator)
            state = _moderator_state(moderator)
            assert state["session"]["phase"] == "verdict"
            for index, detail in enumerate(state["teams"]):
                assert detail["verdictCompletedAt"] is not None
                assert detail["defense"]["answers"] == _defense(index + 1)["answers"]
            _advance(moderator, "leaderboard")
            finalists = [entry["teamId"] for entry in _moderator_state(moderator)["ranking"][:3]]
            _advance(moderator, "finale")
            _advance(moderator, "voting")

            vote_clients = [TestClient(third), TestClient(third)]
            for client in vote_clients:
                before = client.get("/api/vote/state")
                assert before.status_code == 200, before.text
                phone_cookies.append(dict(client.cookies))
            for client in vote_clients:
                vote = client.post("/api/vote", json={"teamId": finalists[0]})
                assert vote.status_code == 200, vote.text
            assert _moderator_state(moderator)["votesCast"] == 2

        # Generation 4: votes survive the restart and the reveal still works.
        fourth = demo_app(SESSION_DB=path)
        with TestClient(fourth) as moderator:
            _login(moderator)
            state = _moderator_state(moderator)
            assert state["session"]["phase"] == "voting"
            assert state["votesCast"] == 2

            phone = TestClient(fourth, cookies=phone_cookies[0])
            vote_state = phone.get("/api/vote/state").json()
            assert vote_state["hasVoted"] is True
            assert vote_state["votedTeamId"] == finalists[0]

            _advance(moderator, "winner")
            reveal = TestClient(fourth).get("/api/vote/state").json()
            assert reveal["open"] is False
            assert sum(entry["audienceVotes"] for entry in reveal["finalists"]) == 2
            assert reveal["finalists"][0]["isWinner"] is True
