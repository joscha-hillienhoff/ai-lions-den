"""A complete session over the HTTP API with several clients polling at once.

Each simulated participant is a separate client with its own cookies: four
teams, the moderator, the projector and audience phones. The tests only use the
public API and the deterministic demo jury, so they stay fast and free.
"""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient

from backend.personas import INVESTOR_IDS

from .helpers import (
    TEST_MODERATOR_PASSWORD,
    api_client,
    api_clients,
    demo_app,
    wait_for_async,
)

PASSWORD = TEST_MODERATOR_PASSWORD


def _pitch(index: int) -> dict[str, str]:
    return {
        "startupName": f"Startup {index}",
        "tagline": f"The focused idea number {index}.",
        "problem": f"Customers number {index} lose time and money every day.",
        "solution": f"A product that removes that work for customer group {index}.",
        "customers": f"Mid-sized companies in segment {index} in Germany.",
        "businessModel": f"A subscription of {index}00 EUR per month per customer.",
    }


SPECIAL_PITCH = {
    "startupName": "Löwenline 🦁",
    "tagline": "Rückgabe in einem Klick - Ärger adé 🚀",
    "problem": "Modehändler verlieren Marge durch Retouren, und Kund:innen warten Wochen.",
    "solution": (
        "Ein Klick macht aus einer Retoure einen lokalen Wiederverkauf. Straße, Größe, Grüße."
    ),
    "customers": "Mittelgroße Modehändler in Deutschland, Österreich und der Schweiz.",
    "businessModel": "SaaS-Gebühr plus 8 % Anteil an jedem wiederverkauften Artikel. Ä ö ü ß 🦁",
}


def _defense(index: int) -> dict[str, Any]:
    prefix = f"Team {index} answer"
    return {
        "answers": [
            f"{prefix} 1: Wir starten mit drei Pilotkunden und messen die Rückgabequote. äöü 🚀",
            f"{prefix} 2: Der Preis liegt bei 900 EUR pro Monat und deckt die Kosten ab.",
            (
                f"{prefix} 3: Unsere Daten zeigen, dass 62 % der Retouren "
                "lokal verkauft werden können."
            ),
            f"{prefix} 4: Der nächste Schritt ist ein Pilot mit einem Partner in Berlin.",
        ],
        "oneChange": f"Team {index} ändert genau eine Sache: den ersten Kundensegment-Fokus. ✅",
    }


async def _advance(moderator: AsyncClient, expected: str) -> dict[str, Any]:
    # The moderator view sends the phase it is showing, like the browser does.
    current = (await _state(moderator, "/api/moderator/state"))["session"]["phase"]
    response = await moderator.post("/api/moderator/phase", json={"expectedPhase": current})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["session"]["phase"] == expected
    return payload


async def test_moderator_phase_advances_are_single_step_and_terminal_safe() -> None:
    """Phase advances require a valid optimistic-concurrency expectation."""
    app = demo_app()
    async with api_clients(app, 3) as (moderator, screen, team):
        login = await moderator.post("/api/moderator/login", json={"password": PASSWORD})
        assert login.status_code == 200, login.text
        await team.post("/api/teams/join", json={"name": "Team One"})
        await _advance(moderator, "ideation")

        # A double click, or a second moderator tab on a slow connection, sends
        # two advances that both chose ideation as their starting point.
        responses = await asyncio.gather(
            *(
                moderator.post("/api/moderator/phase", json={"expectedPhase": "ideation"})
                for _ in range(2)
            )
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert _error_of(list(responses)) == {
            "error": "The phase changed in another tab. Refresh before advancing."
        }

        assert (await _state(moderator, "/api/moderator/state"))["session"]["phase"] == "jury"
        assert (await _state(screen, "/api/screen/state"))["session"]["phase"] == "jury"

        # A stale call afterwards cannot move the session a second time.
        stale = await moderator.post("/api/moderator/phase", json={"expectedPhase": "ideation"})
        assert stale.status_code == 409
        assert (await _state(moderator, "/api/moderator/state"))["session"]["phase"] == "jury"

        # Missing expectedPhase must not provide an unguarded transition path.
        plain = await asyncio.gather(
            moderator.post("/api/moderator/phase"),
            moderator.post("/api/moderator/phase"),
        )
        assert [response.status_code for response in plain] == [400, 400]
        assert {response.json()["error"] for response in plain} == {"Send a valid expectedPhase."}
        invalid = await moderator.post(
            "/api/moderator/phase", json={"expectedPhase": "not-a-phase"}
        )
        assert invalid.status_code == 400
        assert invalid.json() == {"error": "Send a valid expectedPhase."}
        assert (await _state(moderator, "/api/moderator/state"))["session"]["phase"] == "jury"

        for _expected, phase in (
            ("jury", "defense"),
            ("defense", "verdict"),
            ("verdict", "leaderboard"),
            ("leaderboard", "finale"),
            ("finale", "voting"),
            ("voting", "winner"),
        ):
            await _advance(moderator, phase)

        final = await moderator.post("/api/moderator/phase", json={"expectedPhase": "winner"})
        assert final.status_code == 409
        assert final.json() == {"error": "The session is already in its final phase."}


async def _state(client: AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _by_team(details: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {detail["id"]: detail for detail in details}


def _error_of(responses: list[Any]) -> dict[str, Any]:
    return next(response for response in responses if response.status_code == 409).json()


async def test_a_complete_session_is_consistent_across_all_client_views() -> None:
    app = demo_app()
    async with api_clients(app, 6) as clients:
        moderator, screen, team_a, team_b, team_c, team_d = clients

        login = await moderator.post("/api/moderator/login", json={"password": PASSWORD})
        assert login.status_code == 200, login.text

        names = ["Löwen & Söhne 🦁", "Team Beta", "Team Gamma"]
        joins = await asyncio.gather(
            *(
                client.post("/api/teams/join", json={"name": name})
                for client, name in zip((team_a, team_b, team_c), names, strict=True)
            )
        )
        for response in joins:
            assert response.status_code == 200, response.text
        team_ids = [response.json()["team"]["id"] for response in joins]
        assert len(set(team_ids)) == 3

        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["session"]["phase"] == "lobby"
        assert screen_state["counts"]["joined"] == 3
        assert screen_state["maxTeams"] == 20

        # The public voting page has no finalists before the leaderboard.
        vote_state = await _state(screen, "/api/vote/state")
        assert vote_state["open"] is False
        assert vote_state["finalists"] == []
        assert vote_state["votesCast"] == 0
        assert vote_state["hasVoted"] is False

        await _advance(moderator, "ideation")

        # A fourth team may still join during ideation.
        late_ideation = await team_d.post("/api/teams/join", json={"name": "Team Delta"})
        assert late_ideation.status_code == 200, late_ideation.text
        team_ids.append(late_ideation.json()["team"]["id"])

        # Empty and over-long pitches are refused with field errors, not silently stored.
        empty = await team_a.post(
            "/api/teams/pitch",
            json={"pitch": {}, "submit": True, "expectedPitchUpdatedAt": None},
        )
        assert empty.status_code == 400
        assert set(empty.json()["fieldErrors"]) == {
            "startupName",
            "tagline",
            "problem",
            "solution",
            "customers",
            "businessModel",
        }
        too_long = await team_a.post(
            "/api/teams/pitch",
            json={
                "pitch": {**SPECIAL_PITCH, "tagline": "x" * 141},
                "submit": True,
                "expectedPitchUpdatedAt": None,
            },
        )
        assert too_long.status_code == 400
        assert "140" in too_long.json()["fieldErrors"]["tagline"]

        pitches = [SPECIAL_PITCH, _pitch(2), _pitch(3), _pitch(4)]
        team_clients = [team_a, team_b, team_c, team_d]
        submissions = await asyncio.gather(
            *(
                client.post(
                    "/api/teams/pitch",
                    json={
                        "pitch": pitch,
                        "submit": True,
                        "expectedPitchUpdatedAt": None,
                    },
                )
                for client, pitch in zip(team_clients, pitches, strict=True)
            )
        )
        for response in submissions:
            assert response.status_code == 200, response.text

        for client, pitch in zip(team_clients, pitches, strict=True):
            own = (await _state(client, "/api/team/state"))["team"]
            assert own["pitch"] == pitch
            assert isinstance(own["submittedAt"], int)

        moderator_state = await _state(moderator, "/api/moderator/state")
        moderator_teams = _by_team(moderator_state["teams"])
        assert len(moderator_teams) == 4
        for team_id, pitch in zip(team_ids, pitches, strict=True):
            detail = moderator_teams[team_id]
            assert detail["pitch"] == pitch
            assert isinstance(detail["submittedAt"], int)
            assert detail["eligible"] is None

        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["counts"]["joined"] == 4
        assert screen_state["counts"]["submitted"] == 4
        assert [entry["name"] for entry in screen_state["teams"]] == [
            "Löwen & Söhne 🦁",
            "Team Beta",
            "Team Gamma",
            "Team Delta",
        ]
        assert all(entry["submitted"] is True for entry in screen_state["teams"])

        # A team never receives another team's pitch.
        assert b"Team Beta" not in (await team_a.get("/api/team/state")).content

        # A reload keeps the same team and the same submitted pitch.
        current = (await _state(team_a, "/api/team/state"))["team"]
        async with api_client(app, cookies=dict(team_a.cookies)) as reloaded:
            after_reload = (await _state(reloaded, "/api/team/state"))["team"]
            assert after_reload["id"] == current["id"]
            assert after_reload["pitch"] == SPECIAL_PITCH
            assert after_reload["submittedAt"] == current["submittedAt"]

        # Two tabs submitting at the same revision: exactly one write wins.
        version = current["pitchUpdatedAt"]
        duplicate = await asyncio.gather(
            *(
                team_a.post(
                    "/api/teams/pitch",
                    json={
                        "pitch": SPECIAL_PITCH,
                        "submit": True,
                        "expectedPitchUpdatedAt": version,
                    },
                )
                for _ in range(2)
            )
        )
        assert sorted(response.status_code for response in duplicate) == [200, 409]
        assert _error_of(list(duplicate)) == {
            "error": "The pitch changed in another tab. Refresh before saving."
        }

        await _advance(moderator, "jury")

        # Team registration is closed once the jury starts.
        async with api_client(app) as too_late:
            rejected = await too_late.post("/api/teams/join", json={"name": "Team Epsilon"})
            assert rejected.status_code == 409
            assert rejected.json() == {
                "error": "Team registration is closed after the pitch phase."
            }

        # All four team clients read their state while the jury works.
        reads = await asyncio.gather(*(client.get("/api/team/state") for client in team_clients))
        assert [response.status_code for response in reads] == [200] * 4

        async def round_one_done() -> bool:
            view = await _state(moderator, "/api/moderator/state")
            return all(
                all(team["round1"][investor_id]["status"] == "done" for investor_id in INVESTOR_IDS)
                for team in view["teams"]
            )

        await wait_for_async(round_one_done, timeout_ms=15_000)

        moderator_state = await _state(moderator, "/api/moderator/state")
        moderator_teams = _by_team(moderator_state["teams"])
        for team_id, pitch in zip(team_ids, pitches, strict=True):
            detail = moderator_teams[team_id]
            assert detail["eligible"] is True
            assert detail["pitch"] == pitch
            for investor_id in INVESTOR_IDS:
                entry = detail["round1"][investor_id]
                assert entry["status"] == "done"
                assert 1 <= entry["score"] <= 10
                assert entry["assessment"]
                assert entry["question"].endswith("?")

        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["counts"]["eligible"] == 4
        assert screen_state["counts"]["round1Done"] == 4
        assert set(screen_state["round1ByInvestor"].values()) == {4}

        # Scores and questions stay hidden from the team until the defense phase.
        own = (await _state(team_a, "/api/team/state"))["team"]
        for investor_id in INVESTOR_IDS:
            assert own["round1"][investor_id]["status"] == "done"
            assert own["round1"][investor_id]["score"] is None
            assert own["round1"][investor_id]["assessment"] is None
            assert own["round1"][investor_id]["question"] is None

        locked = await team_a.post(
            "/api/teams/pitch",
            json={
                "pitch": SPECIAL_PITCH,
                "submit": True,
                "expectedPitchUpdatedAt": own["pitchUpdatedAt"],
            },
        )
        assert locked.status_code == 409
        assert locked.json() == {"error": "The pitch is locked. The jury phase has started."}

        await _advance(moderator, "defense")

        for client in team_clients:
            own = (await _state(client, "/api/team/state"))["team"]
            for investor_id in INVESTOR_IDS:
                assert 1 <= own["round1"][investor_id]["score"] <= 10
                assert own["round1"][investor_id]["assessment"]
                assert own["round1"][investor_id]["question"].endswith("?")

        # Empty and over-long defense answers are refused without changing state.
        empty_answer = await team_a.post(
            "/api/teams/defense",
            json={
                "answers": ["", "b", "c", "d"],
                "oneChange": "One concrete change.",
                "expectedDefenseSubmittedAt": None,
            },
        )
        assert empty_answer.status_code == 400
        assert empty_answer.json()["fieldErrors"] == {"0": "An answer is required."}

        long_answer = await team_a.post(
            "/api/teams/defense",
            json={
                "answers": ["x" * 1001, "b", "c", "d"],
                "oneChange": "One concrete change.",
                "expectedDefenseSubmittedAt": None,
            },
        )
        assert long_answer.status_code == 400
        assert "1000" in long_answer.json()["fieldErrors"]["0"]

        long_change = await team_a.post(
            "/api/teams/defense",
            json={
                "answers": ["a", "b", "c", "d"],
                "oneChange": "x" * 1001,
                "expectedDefenseSubmittedAt": None,
            },
        )
        assert long_change.status_code == 400
        assert "1000" in long_change.json()["fieldErrors"]["oneChange"]

        still_empty = (await _state(team_a, "/api/team/state"))["team"]["defense"]
        assert still_empty["submittedAt"] is None
        assert still_empty["answers"] == ["", "", "", ""]

        defenses = [_defense(index) for index in range(1, 5)]
        saved = await asyncio.gather(
            *(
                client.post(
                    "/api/teams/defense",
                    json={
                        **defense,
                        "expectedDefenseSubmittedAt": None,
                    },
                )
                for client, defense in zip(team_clients, defenses, strict=True)
            )
        )
        for response in saved:
            assert response.status_code == 200, response.text

        for client, defense in zip(team_clients, defenses, strict=True):
            own = (await _state(client, "/api/team/state"))["team"]
            assert own["defense"]["answers"] == defense["answers"]
            assert own["defense"]["oneChange"] == defense["oneChange"]
            assert isinstance(own["defense"]["submittedAt"], int)

        moderator_state = await _state(moderator, "/api/moderator/state")
        moderator_teams = _by_team(moderator_state["teams"])
        for team_id, defense in zip(team_ids, defenses, strict=True):
            detail = moderator_teams[team_id]
            assert detail["defense"]["answers"] == defense["answers"]
            assert detail["defense"]["oneChange"] == defense["oneChange"]
            assert isinstance(detail["defense"]["submittedAt"], int)

        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["counts"]["defenseSubmitted"] == 4
        assert all(entry["defenseSubmitted"] is True for entry in screen_state["teams"])

        # A second tab answering at the same revision is refused.
        current_defense = (await _state(team_a, "/api/team/state"))["team"]["defense"]
        duplicate = await asyncio.gather(
            *(
                team_a.post(
                    "/api/teams/defense",
                    json={
                        **defenses[0],
                        "expectedDefenseSubmittedAt": current_defense["submittedAt"],
                    },
                )
                for _ in range(2)
            )
        )
        assert sorted(response.status_code for response in duplicate) == [200, 409]

        async with api_client(app, cookies=dict(team_a.cookies)) as reloaded:
            after_reload = (await _state(reloaded, "/api/team/state"))["team"]["defense"]
            assert after_reload["answers"] == defenses[0]["answers"]
            assert after_reload["oneChange"] == defenses[0]["oneChange"]

        await _advance(moderator, "verdict")

        async def verdicts_done() -> bool:
            view = await _state(moderator, "/api/moderator/state")
            return all(team["verdictCompletedAt"] is not None for team in view["teams"])

        await wait_for_async(verdicts_done, timeout_ms=15_000)

        moderator_state = await _state(moderator, "/api/moderator/state")
        moderator_teams = _by_team(moderator_state["teams"])
        for team_id in team_ids:
            detail = moderator_teams[team_id]
            for investor_id in INVESTOR_IDS:
                entry = detail["verdict"][investor_id]
                assert entry["status"] == "done"
                assert entry["statement"]
                assert 1 <= entry["score"] <= 10
                assert entry["decision"] == ("in" if entry["score"] >= 7 else "out")
                assert entry["previousScore"] == detail["round1"][investor_id]["score"]

        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["counts"]["verdictDone"] == 4
        assert set(screen_state["verdictByInvestor"].values()) == {4}

        own = (await _state(team_a, "/api/team/state"))["team"]
        for investor_id in INVESTOR_IDS:
            entry = own["verdict"][investor_id]
            assert entry["status"] == "done"
            assert entry["statement"]
            assert entry["previousScore"] == own["round1"][investor_id]["score"]
        assert b"Startup 2" not in (await team_a.get("/api/team/state")).content

        await _advance(moderator, "leaderboard")

        moderator_state = await _state(moderator, "/api/moderator/state")
        ranking_ids = [entry["teamId"] for entry in moderator_state["ranking"]]
        assert len(ranking_ids) == 4
        screen_state = await _state(screen, "/api/screen/state")
        screen_finalist_ids = [entry["teamId"] for entry in screen_state["finalists"]]
        # The projector lists the finalists neutrally in one random, fixed pitch order.
        assert set(screen_finalist_ids) == set(ranking_ids[:3])
        assert [
            entry["teamId"] for entry in moderator_state["finale"]["finalists"]
        ] == screen_finalist_ids
        assert (await _state(screen, "/api/screen/state"))["finalists"] == screen_state["finalists"]
        for entry in screen_state["finalists"]:
            assert not {"rank", "total", "inCount"} & set(entry)
        assert screen_state["ranking"] == []
        team_leaderboard = await _state(team_a, "/api/team/state")
        assert [entry["teamId"] for entry in team_leaderboard["finalists"]] == screen_finalist_ids
        assert team_leaderboard["ranking"] == [] and team_leaderboard["placement"] is None
        assert all(
            not {"rank", "total", "inCount"} & set(entry) for entry in team_leaderboard["finalists"]
        )
        assert moderator_state["finale"]["finalists"][0]["teamId"] == screen_finalist_ids[0]
        assert all("inCount" in entry for entry in moderator_state["finale"]["finalists"])

        vote_state = await _state(screen, "/api/vote/state")
        vote_finalist_ids = [entry["teamId"] for entry in vote_state["finalists"]]
        assert vote_finalist_ids == screen_finalist_ids
        assert all(
            "total" not in entry and "inCount" not in entry for entry in vote_state["finalists"]
        )
        non_finalist_id = next(
            team_id for team_id in team_ids if team_id not in screen_finalist_ids
        )

        await _advance(moderator, "finale")

        moderator_state = await _state(moderator, "/api/moderator/state")
        assert moderator_state["finale"]["selectedIndex"] == 0
        assert len(moderator_state["finale"]["finalists"]) == 3
        assert (await _state(screen, "/api/screen/state"))["selectedFinalistIndex"] == 0

        selected = await moderator.post("/api/moderator/finale", json={"index": 2})
        assert selected.status_code == 200
        assert selected.json()["finale"]["selectedIndex"] == 2
        # Picking a finalist restarts the 3-minute orientation clock on the server.
        assert selected.json()["session"]["pitchEndsAt"] is not None
        screen_after = await _state(screen, "/api/screen/state")
        assert screen_after["session"]["pitchEndsAt"] == selected.json()["session"]["pitchEndsAt"]
        assert (await _state(screen, "/api/screen/state"))["selectedFinalistIndex"] == 2

        back = await moderator.post("/api/moderator/finale", json={"index": 0})
        assert back.status_code == 200
        assert back.json()["finale"]["selectedIndex"] == 0

        await _advance(moderator, "voting")

        async with api_clients(app, 5) as phones:
            phone_state = await asyncio.gather(*(phone.get("/api/vote/state") for phone in phones))
            assert [response.status_code for response in phone_state] == [200] * 5
            phone_payload = phone_state[0].json()
            assert phone_payload["open"] is True
            assert len(phone_payload["finalists"]) == 3

            first, second = screen_finalist_ids[0], screen_finalist_ids[1]
            outcomes = await asyncio.gather(
                phones[0].post("/api/vote", json={"teamId": first}),
                phones[1].post("/api/vote", json={"teamId": first}),
                phones[2].post("/api/vote", json={"teamId": first}),
            )
            assert [response.status_code for response in outcomes] == [200, 200, 200]

            duplicate_vote = await phones[0].post("/api/vote", json={"teamId": second})
            assert duplicate_vote.status_code == 409
            assert duplicate_vote.json() == {"error": "You have already voted. Your vote is final."}

            # A vote for a team outside the finale is refused but does not use up the vote.
            non_finalist = await phones[3].post("/api/vote", json={"teamId": non_finalist_id})
            assert non_finalist.status_code == 400
            assert non_finalist.json() == {"error": "That team is not in the finale."}
            still_able = await phones[3].post("/api/vote", json={"teamId": second})
            assert still_able.status_code == 200

            # Two submissions from one phone at once: exactly one counts.
            twice = await asyncio.gather(
                phones[4].post("/api/vote", json={"teamId": second}),
                phones[4].post("/api/vote", json={"teamId": second}),
            )
            assert sorted(response.status_code for response in twice) == [200, 409]

        assert (await _state(screen, "/api/vote/state"))["votesCast"] == 5
        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["votesCast"] == 5
        moderator_state = await _state(moderator, "/api/moderator/state")
        assert moderator_state["votesCast"] == 5
        own = await _state(team_a, "/api/team/state")
        assert own["votesCast"] == 5

        await _advance(moderator, "winner")

        vote_state = await _state(screen, "/api/vote/state")
        assert vote_state["open"] is False
        assert len(vote_state["finalists"]) == 3
        assert sum(entry["audienceVotes"] for entry in vote_state["finalists"]) == 5
        assert [entry["isWinner"] for entry in vote_state["finalists"]].count(True) == 1
        assert vote_state["finalists"][0]["teamId"] == first
        assert vote_state["finalists"][0]["audienceVotes"] == 3
        assert vote_state["winnerBasis"] == "audienceVotes"

        screen_state = await _state(screen, "/api/screen/state")
        assert [entry["teamId"] for entry in screen_state["finalists"]] == [
            entry["teamId"] for entry in vote_state["finalists"]
        ]
        assert screen_state["winnerBasis"] == "audienceVotes"

        team_views = await asyncio.gather(
            *(client.get("/api/team/state") for client in team_clients)
        )
        winner_view = next(
            response.json() for response in team_views if response.json()["team"]["id"] == first
        )
        assert winner_view["placement"]["rank"] == ranking_ids.index(first) + 1
        assert winner_view["placement"]["teamId"] == first
        assert len(winner_view["ranking"]) == 4
        # The jury figures appear from the reveal on.
        assert all("inCount" in entry and "total" in entry for entry in vote_state["finalists"])
        assert all("inCount" in entry and "total" in entry for entry in screen_state["finalists"])

        async with api_client(app) as closed_phone:
            await closed_phone.get("/api/vote/state")
            closed = await closed_phone.post("/api/vote", json={"teamId": first})
            assert closed.status_code == 409
            assert closed.json() == {"error": "The audience vote is closed."}

        final = await moderator.post("/api/moderator/phase", json={"expectedPhase": "winner"})
        assert final.status_code == 409
        assert final.json() == {"error": "The session is already in its final phase."}
