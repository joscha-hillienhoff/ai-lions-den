from backend.api import MAX_TEAMS
from backend.finale import clamp_finalist_index
from backend.phase_transition import advance_phase, retreat_phase
from backend.views import moderator_state_view, screen_state_view, team_state_view

from .helpers import (
    TEST_MODERATOR_PASSWORD,
    api_clients,
    demo_app,
    make_config,
    make_state,
    make_team,
    set_verdict,
    write_state,
)


def _ranked_teams():
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


def test_clamps_the_selected_index_to_the_finalist_list() -> None:
    assert clamp_finalist_index(-5, 3) == 0
    assert clamp_finalist_index(0, 3) == 0
    assert clamp_finalist_index(2, 3) == 2
    assert clamp_finalist_index(99, 3) == 2
    assert clamp_finalist_index(1, 0) == 0


def test_clamps_large_integer_without_float_conversion() -> None:
    assert clamp_finalist_index(10**400, 3) == 2


def test_resets_the_spotlight_when_the_finale_phase_starts() -> None:
    state = make_state(_ranked_teams(), "leaderboard")
    state.finale_finalist_index = 2
    outcome = advance_phase(state)
    assert outcome.ok is True and outcome.phase == "finale"
    assert state.finale_finalist_index == 0


def test_freezes_the_leaderboard_roster_when_a_later_verdict_completes() -> None:
    late = make_team(5)
    teams = [*_ranked_teams(), late]
    state = make_state(teams, "verdict")

    outcome = advance_phase(state)
    assert outcome.ok is True and outcome.phase == "leaderboard"
    assert state.leaderboard_team_ids == ["team-1", "team-2", "team-3", "team-4"]

    options = {
        "join_url": "http://localhost:3000/team",
        "qr_data_url": "data:image/png;base64,qr",
        "max_teams": MAX_TEAMS,
    }
    before_screen = screen_state_view(state, **options)
    before_team = team_state_view(state, teams[0])
    before_moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)

    set_verdict(
        late,
        {"growth": 10, "finance": 10, "customer": 10, "technology": 10},
        {"growth": "in", "finance": "in", "customer": "in", "technology": "in"},
    )

    after_screen = screen_state_view(state, **options)
    after_team = team_state_view(state, teams[0])
    after_moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)

    assert after_screen["ranking"] == before_screen["ranking"]
    assert after_screen["finalists"] == before_screen["finalists"]
    assert after_team["ranking"] == before_team["ranking"]
    assert after_team["finalists"] == before_team["finalists"]
    assert after_moderator["ranking"] == before_moderator["ranking"]
    assert after_moderator["finale"]["finalists"] == before_moderator["finale"]["finalists"]


def test_exposes_the_selected_finalist_consistently() -> None:
    teams = _ranked_teams()
    state = make_state(teams, "verdict")
    assert advance_phase(state).ok is True
    assert advance_phase(state).ok is True
    state.finale_finalist_index = 99

    screen = screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        max_teams=MAX_TEAMS,
    )
    assert len(screen["finalists"]) == 3
    assert screen["selectedFinalistIndex"] == 2

    team = team_state_view(state, teams[0])
    assert team["selectedFinalistIndex"] == 2
    assert len(team["finalists"]) == 3

    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    assert len(moderator["finale"]["finalists"]) == 3
    assert moderator["finale"]["selectedIndex"] == 2
    assert all(
        "total" in entry and "inCount" in entry and "rank" in entry
        for entry in moderator["finale"]["finalists"]
    )


def test_the_finale_starts_a_three_minute_orientation_clock_that_never_ends_the_phase() -> None:
    state = make_state(_ranked_teams(), "leaderboard")
    assert advance_phase(state, now=5_000).ok is True

    screen = screen_state_view(
        state, join_url="http://x/team", qr_data_url="data:image/png;base64,qr", max_teams=MAX_TEAMS
    )
    # Entering the finale does not start the clock of the first finalist.
    assert state.finale_pitch_started_at is None
    assert screen["session"]["pitchEndsAt"] is None
    assert screen["session"]["pitchNotStarted"] is True
    # The finale stays untimed: the phase itself has no deadline.
    assert screen["session"]["phaseEndsAt"] is None
    assert team_state_view(state, state.teams[0])["session"]["pitchNotStarted"] is True

    state.finale_pitch_started_at = 5_000
    screen = screen_state_view(
        state, join_url="http://x/team", qr_data_url="data:image/png;base64,qr", max_teams=MAX_TEAMS
    )
    assert screen["session"]["pitchEndsAt"] == 5_000 + 3 * 60_000
    assert screen["session"]["pitchNotStarted"] is False
    assert team_state_view(state, state.teams[0])["session"]["pitchEndsAt"] == 5_000 + 180_000

    # The recorded start is decades before the server clock, so the pitch clock is deep
    # in overtime: the deadline is unchanged and the phase is still the finale.
    assert screen["session"]["serverNow"] > screen["session"]["pitchEndsAt"]
    assert screen["session"]["phase"] == "finale"
    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    assert moderator["session"]["phase"] == "finale"
    assert moderator["session"]["pitchEndsAt"] == 5_000 + 180_000
    assert state.phase == "finale"


def test_the_orientation_clock_is_only_present_in_the_finale() -> None:
    state = make_state(_ranked_teams(), "leaderboard")
    leaderboard = team_state_view(state, state.teams[0])
    assert leaderboard["session"]["pitchEndsAt"] is None


def test_stepping_back_into_the_finale_leaves_the_orientation_clock_not_started() -> None:
    state = make_state(_ranked_teams(), "voting")
    state.finale_pitch_started_at = 5_000

    assert retreat_phase(state, now=900_000).phase == "finale"

    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    assert moderator["session"]["pitchEndsAt"] is None
    assert moderator["session"]["pitchNotStarted"] is True


async def test_picking_a_finalist_restarts_the_orientation_clock_and_keeps_the_phase(
    monkeypatch,
) -> None:
    app = demo_app()
    write_state(app.state.runtime.store, make_state(_ranked_teams(), "leaderboard"))
    clock = {"now": 1_000_000}
    monkeypatch.setattr("backend.api._now_ms", lambda: clock["now"])
    async with api_clients(app, 2) as (moderator, screen):
        await moderator.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        advanced = await moderator.post(
            "/api/moderator/phase", json={"expectedPhase": "leaderboard"}
        )
        assert advanced.status_code == 200, advanced.text

        assert advanced.json()["session"]["pitchEndsAt"] is None
        assert advanced.json()["session"]["pitchNotStarted"] is True

        # The start action starts the clock from the server timestamp; it is idempotent.
        started = await moderator.post("/api/moderator/finale/start")
        assert started.status_code == 200, started.text
        assert started.json()["session"]["pitchEndsAt"] == clock["now"] + 3 * 60_000
        assert started.json()["session"]["pitchNotStarted"] is False
        entered = started.json()["session"]["pitchEndsAt"]
        clock["now"] += 1_000
        again = await moderator.post("/api/moderator/finale/start")
        assert again.json()["session"]["pitchEndsAt"] == entered

        # Five minutes pass: the clock is in overtime, the finale does not move on by itself.
        clock["now"] += 5 * 60_000
        overtime = (await moderator.get("/api/moderator/state")).json()
        assert overtime["session"]["phase"] == "finale"
        assert overtime["session"]["pitchEndsAt"] == entered

        picked = await moderator.post("/api/moderator/finale", json={"index": 1})
        assert picked.status_code == 200, picked.text
        assert picked.json()["session"]["phase"] == "finale"
        assert picked.json()["session"]["pitchEndsAt"] == clock["now"] + 3 * 60_000
        shown = (await screen.get("/api/screen/state")).json()
        assert shown["session"]["pitchEndsAt"] == clock["now"] + 3 * 60_000


async def test_starting_the_pitch_clock_needs_the_moderator_and_the_finale() -> None:
    app = demo_app()
    write_state(app.state.runtime.store, make_state(_ranked_teams(), "leaderboard"))
    async with api_clients(app, 1) as (client,):
        assert (await client.post("/api/moderator/finale/start")).status_code == 401
        await client.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
        assert (await client.post("/api/moderator/finale/start")).status_code == 409
