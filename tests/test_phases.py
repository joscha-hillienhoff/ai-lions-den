from backend.phase_transition import advance_phase
from backend.pitch import pitch_is_complete
from backend.state import PhaseId

from .helpers import COMPLETE_PITCH, make_state, make_team


def test_only_evaluates_submitted_complete_pitches_when_the_jury_starts() -> None:
    complete = make_team(1, eligible=None)
    incomplete = make_team(
        2,
        eligible=None,
        pitch=COMPLETE_PITCH.model_copy(update={"solution": ""}),
    )
    draft_only = make_team(3, eligible=None, submitted_at=None)
    untouched = make_team(
        4,
        eligible=None,
        pitch=COMPLETE_PITCH.model_copy(
            update={
                "startup_name": "",
                "tagline": "",
                "problem": "",
                "solution": "",
                "customers": "",
                "business_model": "",
            }
        ),
        submitted_at=None,
    )

    state = make_state([complete, incomplete, draft_only, untouched])
    ideation = advance_phase(state, 1000)
    assert ideation.ok is True and ideation.phase == "ideation"
    assert all(team.eligible is None for team in state.teams)

    jury = advance_phase(state, 2000)
    assert jury.ok is True and jury.phase == "jury"
    assert state.phase == "jury"
    assert [team.eligible for team in state.teams] == [True, False, False, False]
    assert state.teams[1].round1["growth"].status == "pending"


def test_locks_the_pitch_by_leaving_the_ideation_phase() -> None:
    state = make_state([make_team(1)])
    assert state.phase != "ideation"
    advance_phase(state)
    assert state.phase == "ideation"
    advance_phase(state)
    assert state.phase == "jury"
    assert state.phase != "ideation"


def test_runs_the_phase_order_exactly_once_and_stops_at_the_winner_phase() -> None:
    expected_phases = [
        "lobby",
        "ideation",
        "jury",
        "defense",
        "verdict",
        "leaderboard",
        "finale",
        "voting",
        "winner",
    ]
    state = make_state([], "lobby")
    seen: list[PhaseId] = [state.phase]
    for index in range(len(expected_phases) + 2):
        result = advance_phase(state, 5000 + index)
        if not result.ok:
            assert "final phase" in (result.error or "")
            break
        assert result.phase is not None
        seen.append(result.phase)
    assert seen == expected_phases
    assert state.phase == "winner"
    final = advance_phase(state)
    assert final.ok is False
    assert final.error == "The session is already in its final phase."


def test_resets_stale_round_one_and_verdict_state_of_teams_the_jury_has_not_started_on() -> None:
    team = make_team(1, eligible=None)
    team.verdict_completed_at = 1_700_000_100_000
    state = make_state([team], "ideation")

    advance_phase(state)
    assert state.phase == "jury"
    assert team.eligible is True
    assert team.round1["growth"].status == "pending"
    assert team.verdict["growth"].status == "pending"
    assert team.verdict_completed_at is None


def test_keeps_round_one_results_of_teams_the_jury_already_judged() -> None:
    team = make_team(1)
    team.round1["growth"] = team.round1["growth"].model_copy(
        update={"status": "done", "score": 9, "assessment": "Old", "question": "Old?"}
    )
    state = make_state([team], "ideation")

    advance_phase(state)
    assert state.phase == "jury"
    assert team.round1["growth"].status == "done"
    assert team.round1["growth"].score == 9


def test_recognises_a_complete_pitch_from_the_form_limits() -> None:
    assert pitch_is_complete(COMPLETE_PITCH) is True
    assert pitch_is_complete(COMPLETE_PITCH.model_copy(update={"tagline": "x" * 141})) is False
    assert pitch_is_complete(COMPLETE_PITCH.model_copy(update={"customers": "   "})) is False
