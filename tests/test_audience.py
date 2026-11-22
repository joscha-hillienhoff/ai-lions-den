import json

from backend.api import MAX_TEAMS
from backend.audience import audience_results, jury_finalists, winner_basis
from backend.ranking import ranked_teams
from backend.views import (
    moderator_state_view,
    screen_state_view,
    team_state_view,
    vote_state_view,
)

from .helpers import make_config, make_state, make_team, set_verdict

PHASES_BEFORE_REVEAL = ["jury", "defense", "verdict", "leaderboard", "finale", "voting"]


def _scored(index: int, score: int, *, decisions: str = "in", submitted_at: int | None = None):
    team = make_team(index, submitted_at=submitted_at)
    set_verdict(
        team,
        {"growth": score, "finance": score, "customer": score, "technology": score},
        {
            "growth": decisions,
            "finance": decisions,
            "customer": decisions,
            "technology": decisions,
        },
    )
    return team


def _with_in(index: int, score: int, *, funded: bool):
    team = make_team(index)
    set_verdict(
        team,
        {"growth": score, "finance": score, "customer": score, "technology": score},
        {
            "growth": "in" if funded else "out",
            "finance": "out",
            "customer": "out",
            "technology": "out",
        },
    )
    return team


def _frozen_state(teams, phase: str = "winner", pitch_order_team_ids=None):
    state = make_state(teams, phase, pitch_order_team_ids=pitch_order_team_ids)
    state.leaderboard_team_ids = [entry.team.id for entry in ranked_teams(teams)]
    return state


def _screen(state, votes=None):
    return screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        vote_url="http://localhost:3000/vote",
        vote_qr_data_url="data:image/png;base64,vote",
        vote_counts=votes,
        max_teams=MAX_TEAMS,
    )


def test_finalists_follow_the_deals_first_ranking_order() -> None:
    team_a = _with_in(1, 7, funded=True)
    team_a.pitch.startup_name = "Zephyr"
    team_b = _scored(2, 6, decisions="out")
    team_b.pitch.startup_name = "Atlas"
    team_c = _with_in(3, 8, funded=True)
    team_c.pitch.startup_name = "Meridian"
    team_d = _scored(4, 7, decisions="out")
    team_d.pitch.startup_name = "Beacon"

    state = _frozen_state([team_a, team_b, team_c, team_d], "finale")
    selected = jury_finalists(state)

    assert [entry.team.id for entry in selected] == [team_c.id, team_a.id, team_d.id]
    assert [entry.team.pitch.startup_name for entry in selected] == [
        "Meridian",
        "Zephyr",
        "Beacon",
    ]


def test_finalists_need_one_in_and_can_exclude_higher_ranked_teams() -> None:
    top = _with_in(1, 10, funded=False)
    second = _with_in(2, 9, funded=False)
    third = _with_in(3, 8, funded=True)
    fourth = _with_in(4, 7, funded=True)
    fifth = _with_in(5, 6, funded=True)

    state = _frozen_state([top, second, third, fourth, fifth])
    selected = jury_finalists(state)

    assert [entry.team.id for entry in selected] == [third.id, fourth.id, fifth.id]
    assert all(entry.in_count > 0 for entry in selected)


def test_fewer_than_three_funded_teams_are_filled_from_the_ranking() -> None:
    first = _with_in(1, 10, funded=False)
    second = _with_in(2, 9, funded=False)
    qualified = _with_in(3, 8, funded=True)
    fourth = _with_in(4, 7, funded=False)
    fifth = _with_in(5, 6, funded=False)

    state = _frozen_state([first, second, qualified, fourth, fifth])
    selected = jury_finalists(state)

    assert [entry.team.id for entry in selected] == [qualified.id, first.id, second.id]
    assert len(selected) == 3


def test_no_funded_team_still_fills_the_finale_from_the_ranking() -> None:
    first = _with_in(1, 10, funded=False)
    second = _with_in(2, 9, funded=False)
    third = _with_in(3, 8, funded=False)
    fourth = _with_in(4, 7, funded=False)

    state = _frozen_state([first, second, third, fourth])
    selected = jury_finalists(state)

    assert [entry.team.id for entry in selected] == [first.id, second.id, third.id]
    assert all(entry.in_count == 0 for entry in selected)


def test_the_leaderboard_names_the_finalists_neutrally_in_the_pitch_order() -> None:
    top = _with_in(1, 10, funded=False)
    second = _with_in(2, 9, funded=False)
    qualified = _with_in(3, 8, funded=True)
    fourth = _with_in(4, 7, funded=True)

    pitch_order = [top.id, qualified.id, fourth.id]
    state = _frozen_state([top, second, qualified, fourth], "leaderboard", pitch_order)
    # The jury ranking selects these finalists, but no public view shows its order.
    assert [entry.team.id for entry in jury_finalists(state)] == [qualified.id, fourth.id, top.id]
    expected = pitch_order

    views = [
        _screen(state),
        team_state_view(state, top),
        vote_state_view(state, voted_team_id=None),
    ]
    for view in views:
        assert [entry["teamId"] for entry in view["finalists"]] == expected
        assert all(not {"total", "inCount", "rank"} & set(entry) for entry in view["finalists"])
    # The moderator keeps the figures, in the same order the projector shows.
    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    finalists = moderator["finale"]["finalists"]
    assert [entry["teamId"] for entry in finalists] == expected
    assert [entry["inCount"] for entry in finalists] == [0, 1, 1]
    assert [entry["rank"] for entry in finalists] == [3, 1, 2]


def test_the_audience_winner_has_the_most_votes() -> None:
    first = _scored(1, 10)
    second = _scored(2, 9)
    third = _scored(3, 8)
    state = _frozen_state([first, second, third])

    results = audience_results(state, {second.id: 7, third.id: 2, first.id: 1})
    assert results[0].entry.team.id == second.id
    assert results[0].is_winner is True
    assert results[0].votes == 7
    assert results[0].share == 70.0
    assert sum(result.share for result in results) == 100.0


def test_an_audience_tie_goes_to_the_higher_jury_total() -> None:
    higher = _scored(1, 10)
    lower = _scored(2, 8)
    third = _scored(3, 7)
    state = _frozen_state([higher, lower, third])

    results = audience_results(state, {lower.id: 4, higher.id: 4})
    assert results[0].entry.team.id == higher.id
    assert results[0].votes == 4
    assert results[0].entry.total == 40


def test_an_audience_tie_goes_to_the_team_with_more_deals() -> None:
    team_a = _with_in(1, 7, funded=True)
    team_b = _scored(2, 6, decisions="out")
    third = _with_in(3, 8, funded=True)
    state = _frozen_state([team_a, team_b, third])

    results = audience_results(state, {team_b.id: 5, team_a.id: 5})
    assert results[0].entry.team.id == team_a.id
    assert results[0].entry.in_count == 1
    assert results[1].entry.in_count == 0
    assert winner_basis(results) == "juryInCount"


def test_a_full_tie_keeps_the_existing_jury_ranking_order() -> None:
    earlier = _scored(1, 8, submitted_at=1_000)
    later = _scored(2, 8, submitted_at=2_000)
    third = _scored(3, 7)
    state = _frozen_state([earlier, later, third])

    results = audience_results(state, {later.id: 5, earlier.id: 5})
    assert results[0].entry.team.id == earlier.id
    assert results[0].entry.total == results[1].entry.total
    assert results[0].entry.in_count == results[1].entry.in_count


def test_zero_votes_fall_back_to_the_jury_ranking() -> None:
    first = _scored(1, 10)
    second = _scored(2, 9)
    third = _scored(3, 8)
    state = _frozen_state([first, second, third])

    results = audience_results(state, {})
    assert results[0].entry.team.id == first.id
    assert [result.votes for result in results] == [0, 0, 0]
    assert [result.share for result in results] == [0.0, 0.0, 0.0]


def test_a_clear_audience_winner_is_decided_by_the_most_votes() -> None:
    first = _scored(1, 10)
    second = _scored(2, 9)
    third = _scored(3, 8)
    state = _frozen_state([first, second, third])

    results = audience_results(state, {second.id: 7, third.id: 2, first.id: 1})
    assert winner_basis(results) == "audienceVotes"


def test_an_audience_vote_tie_is_decided_by_the_higher_jury_total() -> None:
    higher = _scored(1, 10)
    lower = _scored(2, 8)
    third = _scored(3, 7)
    state = _frozen_state([higher, lower, third])

    results = audience_results(state, {lower.id: 4, higher.id: 4})
    assert winner_basis(results) == "juryTotal"


def test_a_vote_tie_with_equal_deals_and_totals_goes_to_the_jury_ranking() -> None:
    earlier = _scored(1, 8, submitted_at=1_000)
    later = _scored(2, 8, submitted_at=2_000)
    third = _scored(3, 7)
    state = _frozen_state([earlier, later, third])

    results = audience_results(state, {later.id: 5, earlier.id: 5})
    assert winner_basis(results) == "juryRanking"


def test_no_audience_votes_are_decided_by_the_jury_ranking() -> None:
    first = _scored(1, 10)
    second = _scored(2, 9)
    third = _scored(3, 8)
    state = _frozen_state([first, second, third])

    results = audience_results(state, {})
    assert winner_basis(results) == "juryRanking"


def test_the_winner_basis_reaches_every_public_reveal_payload() -> None:
    first = _scored(1, 10)
    second = _scored(2, 9)
    third = _scored(3, 8)
    state = _frozen_state([first, second, third])
    votes = {first.id: 7, second.id: 2, third.id: 1}

    screen = _screen(state, votes)
    team_view = team_state_view(state, first, votes)
    vote = vote_state_view(state, voted_team_id=None, vote_counts=votes)

    assert screen["winnerBasis"] == "audienceVotes"
    assert team_view["winnerBasis"] == "audienceVotes"
    assert vote["winnerBasis"] == "audienceVotes"


def test_no_individual_jury_text_leaks_through_a_public_payload_before_the_reveal() -> None:
    teams = [_with_in(index, 11 - index, funded=True) for index in (1, 2, 3, 4)]

    for phase in PHASES_BEFORE_REVEAL:
        state = _frozen_state(teams, phase)
        view = _screen(state, {teams[0].id: 3})
        team_view = team_state_view(state, teams[0], {teams[0].id: 3})
        vote = vote_state_view(state, voted_team_id=None, vote_counts={teams[0].id: 3})

        for payload in (view, vote):
            text = json.dumps(payload)
            assert '"statement"' not in text, phase
            assert '"previousScore"' not in text, phase
            assert '"oneChange"' not in text, phase
            assert '"audienceVotes"' not in text, phase

        # The full ranking appears only at the reveal.
        assert view["ranking"] == [], phase
        assert view["winnerBasis"] is None, phase
        assert team_view["ranking"] == [], phase
        assert team_view["placement"] is None, phase

        if phase in ("jury", "defense", "verdict"):
            assert view["finalists"] == [], phase
            assert team_view["finalists"] == [], phase
            assert vote["finalists"] == [], phase
        else:
            assert len(view["finalists"]) == 3, phase
            assert len(team_view["finalists"]) == 3, phase
            assert len(vote["finalists"]) == 3, phase
            # The vote stays blind to the jury result until the reveal.
            assert all(
                "total" not in entry and "inCount" not in entry and "rank" not in entry
                for entry in vote["finalists"]
            ), phase
