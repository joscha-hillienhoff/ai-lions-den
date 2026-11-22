"""Stepping back one phase to recover from an accidental advance."""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient

from backend.jury.types import Round1Input, VerdictInput
from backend.personas import INVESTOR_IDS
from backend.phase_transition import advance_phase, retreat_phase
from backend.state import PHASE_IDS, PhaseId, Round1Entry, phase_ends_at

from .helpers import (
    COMPLETE_PITCH,
    TEST_MODERATOR_PASSWORD,
    api_clients,
    demo_app,
    make_state,
    make_team,
    set_verdict,
    wait_for_async,
)

BACK = "/api/moderator/phase/back"
FORWARD = "/api/moderator/phase"


def _done_round1(team: Any, score: int = 8) -> None:
    for investor_id in INVESTOR_IDS:
        team.round1[investor_id] = Round1Entry(
            status="done", score=score, assessment="Kept.", question="Kept?"
        )


# -- unit level: the phase pointer ------------------------------------------------------


def test_back_moves_exactly_one_phase_and_never_before_the_first() -> None:
    state = make_state([make_team(1)], "winner")
    visited: list[PhaseId] = [state.phase]
    for _ in range(len(PHASE_IDS) - 1):
        outcome = retreat_phase(state, 1)
        assert outcome.ok is True
        assert outcome.phase == state.phase
        visited.append(state.phase)
    assert visited == list(reversed(PHASE_IDS))

    first = retreat_phase(state, 2)
    assert first.ok is False
    assert first.error == "The session is already in its first phase."
    assert state.phase == "lobby"


def test_back_restarts_the_timer_of_a_timed_phase_and_keeps_untimed_phases_untimed() -> None:
    state = make_state([make_team(1)], "defense", now=1_000)

    assert retreat_phase(state, 5_000).phase == "jury"
    assert state.phase_started_at == 5_000
    assert phase_ends_at(state.phase, state.phase_started_at) == 5_000 + 5 * 60_000

    # Voting (3 min) -> finale (untimed): no deadline, then winner (untimed) -> voting (3 min).
    state = make_state([make_team(1)], "voting", now=1_000)
    assert retreat_phase(state, 9_000).phase == "finale"
    assert state.phase_started_at == 9_000
    assert phase_ends_at(state.phase, state.phase_started_at) is None

    state = make_state([make_team(1)], "winner", now=1_000)
    assert retreat_phase(state, 12_000).phase == "voting"
    assert phase_ends_at(state.phase, state.phase_started_at) == 12_000 + 3 * 60_000


def test_back_deletes_and_recomputes_nothing() -> None:
    team = make_team(1)
    _done_round1(team)
    set_verdict(
        team,
        dict.fromkeys(INVESTOR_IDS, 9),
        dict.fromkeys(INVESTOR_IDS, "in"),
    )
    state = make_state([team, make_team(2), make_team(3)], "winner")
    state.leaderboard_team_ids = ["team-3", "team-1"]
    state.finale_finalist_index = 2
    before = state.model_dump()

    for _ in range(len(PHASE_IDS) - 1):
        assert retreat_phase(state, 7).ok is True

    after = state.model_dump()
    # The finale orientation clock restarts whenever the finale is re-entered.
    for key in ("phase", "phase_started_at", "finale_pitch_started_at"):
        before.pop(key)
        after.pop(key)
    assert after == before


def test_advancing_again_keeps_jury_results_and_eligibility() -> None:
    team = make_team(1, eligible=True)
    _done_round1(team)
    failed = make_team(2, eligible=True)
    _done_round1(failed)
    failed.round1["growth"] = Round1Entry(status="failed", error="Boom")
    state = make_state([team, failed], "defense")

    assert retreat_phase(state).phase == "jury"
    assert retreat_phase(state).phase == "ideation"
    assert advance_phase(state).phase == "jury"

    for kept in (team, failed):
        assert kept.eligible is True
        assert all(entry.status != "pending" for entry in kept.round1.values())
    assert team.round1["growth"].assessment == "Kept."
    assert failed.round1["growth"].status == "failed"


def test_advancing_again_keeps_completed_verdicts() -> None:
    team = make_team(1)
    _done_round1(team)
    set_verdict(
        team,
        dict.fromkeys(INVESTOR_IDS, 8),
        dict.fromkeys(INVESTOR_IDS, "in"),
    )
    state = make_state([team], "leaderboard")

    for _ in range(2):
        retreat_phase(state)
    assert state.phase == "defense"
    for _ in range(2):
        advance_phase(state)
    assert state.phase == "leaderboard"

    assert team.verdict_completed_at is not None
    assert all(entry.status == "done" and entry.score == 8 for entry in team.verdict.values())
    assert state.leaderboard_team_ids == [team.id]


def test_a_team_the_jury_never_started_on_is_evaluated_after_stepping_back() -> None:
    late = make_team(1, eligible=False, submitted_at=None)
    state = make_state([late], "jury")

    retreat_phase(state)
    assert state.phase == "ideation"
    late.submitted_at = 1_700_000_005_000
    late.pitch = COMPLETE_PITCH

    advance_phase(state)
    assert state.phase == "jury"
    assert late.eligible is True


# -- API level --------------------------------------------------------------------------


def _count_provider_calls(runtime: Any) -> dict[str, int]:
    calls = {"round1": 0, "verdict": 0}
    provider = runtime.provider
    round1 = provider.evaluate_round1
    verdict = provider.evaluate_verdict

    async def counted_round1(input_value: Round1Input) -> Any:
        calls["round1"] += 1
        return await round1(input_value)

    async def counted_verdict(input_value: VerdictInput) -> Any:
        calls["verdict"] += 1
        return await verdict(input_value)

    provider.evaluate_round1 = counted_round1
    provider.evaluate_verdict = counted_verdict
    return calls


async def _phase(client: AsyncClient) -> str:
    return (await client.get("/api/moderator/state")).json()["session"]["phase"]


async def _forward(moderator: AsyncClient) -> dict[str, Any]:
    response = await moderator.post(FORWARD, json={"expectedPhase": await _phase(moderator)})
    assert response.status_code == 200, response.text
    return response.json()


async def _back(moderator: AsyncClient) -> dict[str, Any]:
    response = await moderator.post(BACK, json={"expectedPhase": await _phase(moderator)})
    assert response.status_code == 200, response.text
    return response.json()


async def _all_done(moderator: AsyncClient, key: str) -> bool:
    view = (await moderator.get("/api/moderator/state")).json()
    return all(
        all(team[key][investor_id]["status"] == "done" for investor_id in INVESTOR_IDS)
        for team in view["teams"]
        if team["eligible"]
    )


def _pitch(index: int) -> dict[str, str]:
    return {
        "startupName": f"Startup {index}",
        "tagline": f"The focused idea number {index}.",
        "problem": f"Customers number {index} lose time and money every day.",
        "solution": f"A product that removes that work for customer group {index}.",
        "customers": f"Mid-sized companies in segment {index} in Germany.",
        "businessModel": f"A subscription of {index}00 EUR per month per customer.",
    }


def _defense() -> dict[str, Any]:
    return {
        "answers": [f"Answer {number} with a concrete number: 42." for number in range(1, 5)],
        "oneChange": "We focus on a single customer segment first.",
        "expectedDefenseSubmittedAt": None,
    }


async def test_back_is_guarded_by_expected_phase_and_unavailable_in_the_first_phase() -> None:
    app = demo_app()
    async with api_clients(app, 2) as (moderator, other_tab):
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        await other_tab.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})

        first = await moderator.post(BACK, json={"expectedPhase": "lobby"})
        assert first.status_code == 409
        assert first.json() == {"error": "The session is already in its first phase."}

        for _ in range(2):
            await _forward(moderator)
        assert await _phase(moderator) == "jury"

        # A double click, or a second tab, sends two backs from the same phase.
        responses = await asyncio.gather(
            moderator.post(BACK, json={"expectedPhase": "jury"}),
            other_tab.post(BACK, json={"expectedPhase": "jury"}),
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        rejected = next(response for response in responses if response.status_code == 409)
        assert rejected.json() == {
            "error": "The phase changed in another tab. Refresh before going back."
        }
        assert await _phase(moderator) == "ideation"

        # Missing or invalid expectations never move the session.
        for body in (None, {"expectedPhase": "not-a-phase"}, {}):
            response = await moderator.post(BACK, json=body)
            assert response.status_code == 400
            assert response.json() == {"error": "Send a valid expectedPhase."}
        assert await _phase(moderator) == "ideation"


async def test_back_requires_the_moderator() -> None:
    app = demo_app()
    async with api_clients(app, 1) as (anonymous,):
        response = await anonymous.post(BACK, json={"expectedPhase": "ideation"})
        assert response.status_code == 401


async def test_re_advancing_after_back_makes_no_duplicate_jury_calls() -> None:
    app = demo_app()
    calls = _count_provider_calls(app.state.runtime)
    async with api_clients(app, 3) as (moderator, team_a, team_b):
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        for client, index in ((team_a, 1), (team_b, 2)):
            await client.post("/api/teams/join", json={"name": f"Team {index}"})
        await _forward(moderator)  # ideation
        for client, index in ((team_a, 1), (team_b, 2)):
            saved = await client.post(
                "/api/teams/pitch",
                json={"pitch": _pitch(index), "submit": True, "expectedPitchUpdatedAt": None},
            )
            assert saved.status_code == 200, saved.text

        await _forward(moderator)  # jury
        await wait_for_async(lambda: _all_done(moderator, "round1"))
        assert calls == {"round1": 8, "verdict": 0}
        round1_before = (await moderator.get("/api/moderator/state")).json()["teams"]

        await _forward(moderator)  # defense
        for client in (team_a, team_b):
            saved = await client.post("/api/teams/defense", json=_defense())
            assert saved.status_code == 200, saved.text

        await _forward(moderator)  # verdict
        await wait_for_async(lambda: _all_done(moderator, "verdict"))
        assert calls == {"round1": 8, "verdict": 8}
        before = (await moderator.get("/api/moderator/state")).json()["teams"]

        # Step all the way back to ideation, then forward to the leaderboard again.
        for expected in ("defense", "jury", "ideation"):
            assert (await _back(moderator))["session"]["phase"] == expected
        for expected in ("jury", "defense", "verdict", "leaderboard"):
            assert (await _forward(moderator))["session"]["phase"] == expected
        await asyncio.sleep(0.2)
        assert calls == {"round1": 8, "verdict": 8}

        after = (await moderator.get("/api/moderator/state")).json()
        assert after["teams"] == before
        assert [team["round1"] for team in after["teams"]] == [
            team["round1"] for team in round1_before
        ]
        assert [entry["teamId"] for entry in after["ranking"]] == [
            team["id"] for team in sorted(before, key=lambda team: team["rank"])
        ]


async def test_a_failed_step_is_the_only_thing_that_may_run_again() -> None:
    app = demo_app()
    runtime = app.state.runtime
    calls = _count_provider_calls(runtime)
    async with api_clients(app, 2) as (moderator, team):
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        await team.post("/api/teams/join", json={"name": "Team One"})
        await _forward(moderator)
        await team.post(
            "/api/teams/pitch",
            json={"pitch": _pitch(1), "submit": True, "expectedPitchUpdatedAt": None},
        )
        await _forward(moderator)
        await wait_for_async(lambda: _all_done(moderator, "round1"))
        assert calls["round1"] == 4

        # One call fails; stepping back and forward alone does not retry it.
        def fail_one(state: Any) -> None:
            state.teams[0].round1["growth"] = Round1Entry(status="failed", error="Boom")

        runtime.store.update(fail_one)
        await _back(moderator)
        await _forward(moderator)
        await asyncio.sleep(0.2)
        assert calls["round1"] == 4
        view = (await moderator.get("/api/moderator/state")).json()["teams"][0]
        assert view["round1"]["growth"]["status"] == "failed"

        # The existing retry still runs exactly the failed call.
        retry = await moderator.post(
            "/api/moderator/retry", json={"teamId": view["id"], "step": "round1"}
        )
        assert retry.status_code == 200
        await wait_for_async(lambda: _all_done(moderator, "round1"))
        assert calls["round1"] == 5


async def test_back_keeps_a_judged_pitch_and_defense_locked() -> None:
    app = demo_app()
    async with api_clients(app, 2) as (moderator, team):
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        await team.post("/api/teams/join", json={"name": "Team One"})
        await _forward(moderator)
        await team.post(
            "/api/teams/pitch",
            json={"pitch": _pitch(1), "submit": True, "expectedPitchUpdatedAt": None},
        )
        await _forward(moderator)
        await wait_for_async(lambda: _all_done(moderator, "round1"))
        await _forward(moderator)  # defense
        await team.post("/api/teams/defense", json=_defense())
        await _forward(moderator)  # verdict
        await wait_for_async(lambda: _all_done(moderator, "verdict"))

        await _back(moderator)  # defense
        own = (await team.get("/api/team/state")).json()["team"]
        locked = await team.post(
            "/api/teams/defense",
            json={**_defense(), "expectedDefenseSubmittedAt": own["defense"]["submittedAt"]},
        )
        assert locked.status_code == 409
        assert locked.json() == {
            "error": "The answers are locked. The final verdict has already started."
        }

        await _back(moderator)  # jury
        await _back(moderator)  # ideation
        edited = await team.post(
            "/api/teams/pitch",
            json={
                "pitch": _pitch(9),
                "submit": True,
                "expectedPitchUpdatedAt": own["pitchUpdatedAt"],
            },
        )
        assert edited.status_code == 409
        assert edited.json() == {"error": "The pitch is locked. The jury has already evaluated it."}
        assert (await team.get("/api/team/state")).json()["team"]["pitch"] == _pitch(1)


async def test_winner_back_to_voting_keeps_figures_hidden_and_votes_counted() -> None:
    app = demo_app()
    async with api_clients(app, 8) as clients:
        moderator, screen, *teams_and_voters = clients
        team_clients = teams_and_voters[:4]
        voter = teams_and_voters[4]
        voter_two = teams_and_voters[5]
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        for index, client in enumerate(team_clients, start=1):
            await client.post("/api/teams/join", json={"name": f"Team {index}"})
        await _forward(moderator)
        for index, client in enumerate(team_clients, start=1):
            await client.post(
                "/api/teams/pitch",
                json={"pitch": _pitch(index), "submit": True, "expectedPitchUpdatedAt": None},
            )
        await _forward(moderator)
        await wait_for_async(lambda: _all_done(moderator, "round1"))
        await _forward(moderator)
        for client in team_clients:
            await client.post("/api/teams/defense", json=_defense())
        await _forward(moderator)
        await wait_for_async(lambda: _all_done(moderator, "verdict"))
        await _forward(moderator)  # leaderboard
        await _forward(moderator)  # finale
        await _forward(moderator)  # voting

        finalists = (await voter.get("/api/vote/state")).json()["finalists"]
        assert len(finalists) == 3
        order_in_voting = [entry["teamId"] for entry in finalists]
        cast = await voter.post("/api/vote", json={"teamId": order_in_voting[0]})
        assert cast.status_code == 200, cast.text

        await _forward(moderator)  # winner
        winner_view = (await screen.get("/api/screen/state")).json()
        assert winner_view["session"]["phase"] == "winner"
        assert winner_view["votesCast"] == 1

        started = (await _back(moderator))["session"]
        assert started["phase"] == "voting"
        assert started["phaseEndsAt"] == started["phaseStartedAt"] + 3 * 60_000

        # No jury figure or ranking position leaks on any public view after stepping back.
        for client, path in (
            (screen, "/api/screen/state"),
            (voter, "/api/vote/state"),
            (team_clients[0], "/api/team/state"),
        ):
            payload = (await client.get(path)).json()
            assert payload["session"]["phase"] == "voting"
            assert payload.get("ranking", []) == []
            assert payload.get("winnerBasis") is None
            assert payload.get("placement") is None
            for entry in payload["finalists"]:
                for hidden in ("inCount", "total", "rank", "scores", "audienceVotes", "juryRank"):
                    assert hidden not in entry
        assert [
            entry["teamId"] for entry in (await voter.get("/api/vote/state")).json()["finalists"]
        ] == order_in_voting

        # Votes already cast stay counted, and voting is open again for a new voter.
        assert (await voter.get("/api/vote/state")).json()["hasVoted"] is True
        assert (await screen.get("/api/screen/state")).json()["votesCast"] == 1
        second = await voter_two.post("/api/vote", json={"teamId": order_in_voting[1]})
        assert second.status_code == 200, second.text
        assert (await screen.get("/api/screen/state")).json()["votesCast"] == 2

        # Forward again: the reveal shows both votes.
        await _forward(moderator)
        final = (await screen.get("/api/screen/state")).json()
        assert final["session"]["phase"] == "winner"
        assert final["votesCast"] == 2


# -- the team view tells a judged team its forms are locked ---------------------------


def test_team_view_locks_the_pitch_and_answers_only_once_the_jury_started_on_them() -> None:
    from backend.views import team_state_view

    untouched = make_team(1)
    judged = make_team(2)
    _done_round1(judged)
    sealed = make_team(3)
    _done_round1(sealed)
    set_verdict(sealed, {i: 7 for i in INVESTOR_IDS}, {i: "in" for i in INVESTOR_IDS})
    state = make_state([untouched, judged, sealed], "ideation")

    def locks(team: Any) -> tuple[Any, Any]:
        view = team_state_view(state, team)["team"]
        assert isinstance(view, dict)
        defense = view["defense"]
        assert isinstance(defense, dict)
        return view["pitchLocked"], defense["locked"]

    assert locks(untouched) == (False, False)
    assert locks(judged) == (True, False)
    assert locks(sealed) == (True, True)

    # A verdict call in flight already counts as the answers having been read.
    in_flight = make_team(4)
    _done_round1(in_flight)
    in_flight.verdict[INVESTOR_IDS[0]].status = "running"
    assert locks(in_flight) == (True, True)
