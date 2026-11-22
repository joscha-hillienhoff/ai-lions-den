import pytest

from backend.api import MAX_TEAMS
from backend.state import PHASE_IDS, PHASES, phase_ends_at, phase_info
from backend.views import (
    moderator_state_view,
    screen_state_view,
    session_view,
    team_state_view,
    vote_state_view,
)

from .helpers import make_config, make_state, make_team

MINUTE_MS = 60_000
STARTED_AT = 1_700_000_000_000

TIMED_MINUTES = {
    "lobby": 5,
    "ideation": 20,
    "jury": 5,
    "defense": 10,
    "verdict": 5,
    "leaderboard": 3,
    "voting": 3,
}
UNTIMED_PHASES = ("finale", "winner")


def test_every_phase_is_either_timed_or_explicitly_untimed() -> None:
    assert set(TIMED_MINUTES) | set(UNTIMED_PHASES) == set(PHASE_IDS)
    assert len(PHASES) == len(PHASE_IDS)


@pytest.mark.parametrize(("phase", "minutes"), TIMED_MINUTES.items())
def test_timed_phases_have_their_configured_duration(phase: str, minutes: int) -> None:
    assert phase_info(phase)["minutes"] == minutes  # type: ignore[arg-type]
    assert phase_ends_at(phase, STARTED_AT) == STARTED_AT + minutes * MINUTE_MS  # type: ignore[arg-type]


def test_the_timed_phases_add_up_to_fifty_one_minutes() -> None:
    assert sum(TIMED_MINUTES.values()) == 51


@pytest.mark.parametrize("phase", UNTIMED_PHASES)
def test_untimed_phases_have_no_deadline(phase: str) -> None:
    assert phase_info(phase)["minutes"] is None  # type: ignore[arg-type]
    assert phase_ends_at(phase, STARTED_AT) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("phase", PHASE_IDS)
def test_the_session_view_reports_the_deadline_or_null_in_every_view(phase: str) -> None:
    teams = [make_team(1), make_team(2), make_team(3)]
    state = make_state(teams, phase, STARTED_AT)
    expected_minutes = TIMED_MINUTES.get(phase)
    expected_ends_at = (
        None if expected_minutes is None else STARTED_AT + expected_minutes * MINUTE_MS
    )

    sessions = [
        session_view(state),
        screen_state_view(
            state,
            join_url="http://localhost:3000/team",
            qr_data_url="data:image/png;base64,qr",
            vote_url="http://localhost:3000/vote",
            vote_qr_data_url="data:image/png;base64,vote",
            max_teams=MAX_TEAMS,
        )["session"],
        team_state_view(state, teams[0])["session"],
        vote_state_view(state, voted_team_id=None)["session"],
        moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)["session"],
    ]
    for session in sessions:
        assert session["minutes"] == expected_minutes
        assert session["phaseEndsAt"] == expected_ends_at
