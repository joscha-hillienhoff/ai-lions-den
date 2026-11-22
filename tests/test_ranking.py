from backend.ranking import finalists, ranked_teams

from .helpers import make_team, set_verdict


def _scored_team(index, scores, decisions, submitted_at):
    team = make_team(index, submitted_at=submitted_at)
    set_verdict(
        team,
        {
            "growth": scores[0],
            "finance": scores[1],
            "customer": scores[2],
            "technology": scores[3],
        },
        {
            "growth": decisions[0],
            "finance": decisions[1],
            "customer": decisions[2],
            "technology": decisions[3],
        },
    )
    return team


def test_ranks_by_the_number_of_in_decisions_before_the_jury_total() -> None:
    # The Team A / Team B example from the launch brief: A has fewer points but
    # one deal, B has more points but no deal. Deals count first.
    team_a = _scored_team(1, [7, 4, 4, 4], ["in", "out", "out", "out"], 100)
    team_b = _scored_team(2, [6, 6, 6, 6], ["out", "out", "out", "out"], 900)
    ranked = ranked_teams([team_b, team_a])

    assert [entry.team.id for entry in ranked] == [team_a.id, team_b.id]
    assert ranked[0].total == 19
    assert ranked[0].in_count == 1
    assert ranked[1].total == 24
    assert ranked[1].in_count == 0


def test_breaks_a_deal_tie_on_the_jury_total() -> None:
    low = _scored_team(1, [5, 5, 5, 5], ["in", "in", "in", "in"], 100)
    high = _scored_team(2, [9, 8, 9, 8], ["in", "in", "in", "in"], 900)
    ranked = ranked_teams([low, high])

    assert [entry.team.id for entry in ranked] == [high.id, low.id]
    assert ranked[0].total == 34
    assert ranked[0].in_count == 4
    assert ranked[1].total == 20


def test_breaks_a_remaining_tie_on_the_earlier_pitch_submission() -> None:
    later = _scored_team(1, [8, 8, 7, 7], ["in", "out", "in", "out"], 2_000)
    earlier = _scored_team(2, [8, 8, 7, 7], ["in", "out", "in", "out"], 1_000)
    ranked = ranked_teams([later, earlier])

    assert ranked[0].team.id == earlier.id
    assert ranked[1].team.id == later.id


def test_excludes_ineligible_and_unfinished_teams_and_names_the_top_three() -> None:
    first = _scored_team(1, [9, 9, 9, 9], ["in"] * 4, 100)
    second = _scored_team(2, [8, 8, 8, 8], ["in"] * 4, 200)
    third = _scored_team(3, [7, 7, 7, 7], ["in"] * 4, 300)
    fourth = _scored_team(4, [6, 6, 6, 6], ["in"] * 4, 400)
    ineligible = _scored_team(5, [10, 10, 10, 10], ["in"] * 4, 50)
    ineligible.eligible = False
    unfinished = make_team(6, eligible=True)
    unfinished.round1["growth"] = unfinished.round1["growth"].model_copy(
        update={"status": "done", "score": 10, "assessment": "Great.", "question": "Why?"}
    )
    unfinished.verdict["growth"] = unfinished.verdict["growth"].model_copy(
        update={"status": "done", "score": 10, "decision": "in", "statement": "In."}
    )

    ranked = ranked_teams([ineligible, unfinished, fourth, third, second, first])
    assert len(ranked) == 4
    assert [
        entry.team.id for entry in finalists([ineligible, unfinished, fourth, third, second, first])
    ] == [first.id, second.id, third.id]


def test_finalists_are_the_top_three_of_the_same_ranking() -> None:
    top = _scored_team(1, [10, 10, 10, 10], ["out"] * 4, 100)
    second = _scored_team(2, [9, 9, 9, 9], ["out"] * 4, 200)
    qualified = _scored_team(3, [8, 8, 8, 8], ["in", "out", "out", "out"], 300)
    fourth = _scored_team(4, [7, 7, 7, 7], ["out"] * 4, 400)

    selected = finalists([top, second, qualified, fourth])
    assert [entry.team.id for entry in selected] == [qualified.id, top.id, second.id]


def test_finalists_fill_from_the_same_ranking_when_no_team_has_a_deal() -> None:
    first = _scored_team(1, [10, 10, 10, 10], ["out"] * 4, 100)
    second = _scored_team(2, [9, 9, 9, 9], ["out"] * 4, 200)
    third = _scored_team(3, [8, 8, 8, 8], ["out"] * 4, 300)

    selected = finalists([third, second, first])
    assert [entry.team.id for entry in selected] == [first.id, second.id, third.id]
