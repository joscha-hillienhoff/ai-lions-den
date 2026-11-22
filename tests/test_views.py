import json

from backend.api import MAX_TEAMS
from backend.state import Round1Entry
from backend.views import (
    moderator_state_view,
    screen_state_view,
    team_state_view,
    vote_state_view,
)

from .helpers import make_config, make_state, make_team, set_verdict


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


def _moderator(state):
    return moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)


def _screen(state):
    return screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        vote_url="http://localhost:3000/vote",
        vote_qr_data_url="data:image/png;base64,vote",
        max_teams=MAX_TEAMS,
    )


JURY_KEYS = {"rank", "total", "inCount", "audienceVotes", "audienceShare", "isWinner"}


def test_pre_reveal_views_list_the_finalists_neutrally_in_the_pitch_order() -> None:
    teams = _teams()
    # Make the jury order differ from registration order: the last team ranks first.
    set_verdict(
        teams[3],
        {"growth": 10, "finance": 10, "customer": 10, "technology": 10},
        {"growth": "in", "finance": "in", "customer": "in", "technology": "in"},
    )
    set_verdict(
        teams[0],
        {"growth": 1, "finance": 1, "customer": 1, "technology": 1},
        {"growth": "out", "finance": "out", "customer": "out", "technology": "out"},
    )
    jury_ids = [
        entry["teamId"] for entry in _moderator(make_state(teams, "leaderboard"))["ranking"]
    ]
    assert jury_ids[0] == teams[3].id
    pitch_ids = [teams[2].id, teams[3].id, teams[1].id]

    for phase in ("leaderboard", "finale", "voting"):
        state = make_state(teams, phase, pitch_order_team_ids=pitch_ids)
        screen = _screen(state)
        team_view = team_state_view(state, teams[3])
        vote = vote_state_view(state, voted_team_id=None)
        finalist_ids = [entry["teamId"] for entry in screen["finalists"]]
        assert finalist_ids == pitch_ids
        assert set(finalist_ids) == set(jury_ids[:3])
        assert finalist_ids != jury_ids[:3]
        for view in (screen, team_view, vote):
            assert [entry["teamId"] for entry in view["finalists"]] == finalist_ids
            for entry in view["finalists"]:
                assert not JURY_KEYS & set(entry)
            assert view.get("winnerBasis") is None
        assert screen["ranking"] == []
        assert team_view["ranking"] == []
        assert team_view["placement"] is None
        # No jury figure or ranking position appears anywhere in the public payloads.
        for view in (screen, team_view, vote):
            payload = json.dumps(view)
            assert '"rank"' not in payload
            assert '"inCount"' not in payload
            assert '"total"' not in payload
            assert '"isTopThree"' not in payload


def test_team_view_keeps_the_teams_own_verdict_before_the_reveal() -> None:
    teams = _teams()
    state = make_state(teams, "leaderboard")
    view = team_state_view(state, teams[0])

    scores = [entry["score"] for entry in view["team"]["verdict"].values()]
    assert scores == [9, 9, 9, 9]
    # Jury figures never ride along in the public finalist list.
    assert view["finalists"]
    for entry in view["finalists"]:
        assert not JURY_KEYS & set(entry)


def test_moderator_keeps_all_figures_in_the_projector_order() -> None:
    teams = _teams()
    pitch_ids = [teams[1].id, teams[2].id, teams[0].id]
    moderator = _moderator(make_state(teams, "finale", pitch_order_team_ids=pitch_ids))

    assert len(moderator["ranking"]) == 4
    finalists = moderator["finale"]["finalists"]
    assert [entry["teamId"] for entry in finalists] == pitch_ids
    assert all({"rank", "total", "inCount"} <= set(entry) for entry in finalists)
    screen = _screen(make_state(teams, "finale", pitch_order_team_ids=pitch_ids))
    assert [entry["teamId"] for entry in screen["finalists"]] == [
        entry["teamId"] for entry in finalists
    ]


def test_names_the_finalists_in_the_pitch_order() -> None:
    teams = _teams()
    teams[0].pitch.startup_name = "Zephyr"
    teams[1].pitch.startup_name = "Atlas"
    teams[2].pitch.startup_name = "Meridian"

    pitch_ids = [teams[2].id, teams[0].id, teams[1].id]
    view = _screen(make_state(teams, "finale", pitch_order_team_ids=pitch_ids))
    assert [entry["startupName"] for entry in view["finalists"]] == [
        "Meridian",
        "Zephyr",
        "Atlas",
    ]


def test_publishes_the_reveal_only_in_the_winner_phase() -> None:
    teams = _teams()
    state = make_state(teams, "winner")
    state.leaderboard_team_ids = [team.id for team in teams]
    votes = {teams[2].id: 5, teams[1].id: 3, teams[0].id: 1}

    view = screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        vote_url="http://localhost:3000/vote",
        vote_qr_data_url="data:image/png;base64,vote",
        vote_counts=votes,
        max_teams=MAX_TEAMS,
    )

    assert len(view["ranking"]) == 4
    assert len(view["finalists"]) == 3
    assert all(
        {"rank", "total", "inCount", "audienceVotes"} <= set(entry) for entry in view["finalists"]
    )
    assert [entry["isWinner"] for entry in view["finalists"]] == [True, False, False]
    assert view["votesCast"] == 9

    team_view = team_state_view(state, teams[0], votes)
    assert len(team_view["ranking"]) == 4
    assert team_view["placement"] is not None
    assert team_view["votesCast"] == 9
    assert all(
        {"rank", "total", "inCount", "audienceVotes"} <= set(entry)
        for entry in team_view["finalists"]
    )


def test_publishes_score_movement_to_the_team_and_moderator_only() -> None:
    team = make_team(1)
    for investor_id in ("growth", "finance", "customer", "technology"):
        team.round1[investor_id] = Round1Entry(
            status="done",
            score=5,
            assessment="An assessment.",
            question="A question?",
        )
    set_verdict(
        team,
        {"growth": 7, "finance": 3, "customer": 5, "technology": 6},
        {"growth": "in", "finance": "out", "customer": "in", "technology": "in"},
    )
    state = make_state([team], "verdict")

    view = team_state_view(state, team)
    assert view["team"] is not None
    verdict = view["team"]["verdict"]
    assert verdict["growth"]["previousScore"] == 5
    assert verdict["growth"]["score"] == 7
    assert verdict["finance"]["previousScore"] == 5
    assert verdict["finance"]["score"] == 3
    assert verdict["customer"]["previousScore"] == 5
    assert verdict["customer"]["score"] == 5
    assert view["team"]["defense"]["oneChange"] == "A concrete change from the feedback."

    early = team_state_view(make_state([team], "jury"), team)
    assert early["team"] is not None
    assert all(
        entry["score"] is None and entry["previousScore"] is None
        for entry in early["team"]["verdict"].values()
    )

    moderator = moderator_state_view(state, make_config(), max_teams=MAX_TEAMS)
    detail = moderator["teams"][0]
    assert detail["verdict"]["growth"]["previousScore"] == 5
    assert detail["verdict"]["growth"]["score"] == 7
    assert detail["defense"]["oneChange"] == "A concrete change from the feedback."


def test_public_payloads_still_hide_the_verdict_and_movement() -> None:
    team = make_team(1)
    set_verdict(
        team,
        {"growth": 7, "finance": 3, "customer": 5, "technology": 6},
        {"growth": "in", "finance": "out", "customer": "in", "technology": "in"},
    )
    state = make_state([team], "leaderboard")
    screen = screen_state_view(
        state,
        join_url="http://localhost:3000/team",
        qr_data_url="data:image/png;base64,qr",
        vote_url="http://localhost:3000/vote",
        vote_qr_data_url="data:image/png;base64,vote",
        max_teams=MAX_TEAMS,
    )
    vote = vote_state_view(state, voted_team_id=None)
    for payload in (screen, vote):
        text = json.dumps(payload)
        assert '"previousScore"' not in text
        assert '"oneChange"' not in text
        assert '"statement"' not in text
        assert '"score"' not in text


def _teams_with_feedback():
    """Four teams with distinct, searchable jury feedback and defense text."""
    teams = _teams()
    for team in teams:
        index = team.id.removeprefix("team-")
        team.pitch.problem = f"Problem text of team {index}."
        team.defense.answers = [f"Answer {n} of team {index}." for n in range(4)]
        team.defense.one_change = f"One change of team {index}."
        for investor_id in ("growth", "finance", "customer", "technology"):
            team.round1[investor_id] = Round1Entry(
                status="done",
                score=6,
                assessment=f"Assessment of team {index} by {investor_id}.",
                question=f"Question to team {index} by {investor_id}?",
            )
            team.verdict[investor_id].statement = f"Verdict of team {index} by {investor_id}."
    return teams


def test_team_view_carries_the_pitch_and_jury_feedback_for_the_pitch_card() -> None:
    teams = _teams_with_feedback()
    pitch_ids = [teams[0].id, teams[1].id, teams[2].id]

    for phase in ("leaderboard", "finale", "voting"):
        state = make_state(teams, phase, pitch_order_team_ids=pitch_ids)
        # A finalist and the one team outside the top three get the same own data.
        for team in (teams[0], teams[3]):
            index = team.id.removeprefix("team-")
            own = team_state_view(state, team)["team"]
            assert own["pitch"]["startupName"] == f"Startup {index}"
            assert own["pitch"]["problem"] == f"Problem text of team {index}."
            assert own["defense"]["oneChange"] == f"One change of team {index}."
            assert own["defense"]["answers"][0] == f"Answer 0 of team {index}."
            for investor_id in ("growth", "finance", "customer", "technology"):
                assert own["round1"][investor_id]["question"] == (
                    f"Question to team {index} by {investor_id}?"
                )
                assert own["verdict"][investor_id]["statement"] == (
                    f"Verdict of team {index} by {investor_id}."
                )


def test_team_view_only_carries_the_viewing_teams_own_data_before_the_reveal() -> None:
    teams = _teams_with_feedback()
    pitch_ids = [teams[0].id, teams[1].id, teams[2].id]

    for phase in ("leaderboard", "finale", "voting"):
        state = make_state(teams, phase, pitch_order_team_ids=pitch_ids)
        for viewer in (teams[0], teams[3]):
            view = team_state_view(state, viewer)
            payload = json.dumps(view)
            own_index = viewer.id.removeprefix("team-")
            for other in teams:
                other_index = other.id.removeprefix("team-")
                if other_index == own_index:
                    continue
                # Nothing but the neutral finalist entry of another team is visible: no
                # pitch body, defense, jury feedback or figures.
                for marker in (
                    f"Problem text of team {other_index}.",
                    f"Answer 0 of team {other_index}.",
                    f"One change of team {other_index}.",
                    f"Question to team {other_index} by",
                    f"Assessment of team {other_index} by",
                    f"Verdict of team {other_index} by",
                ):
                    assert marker not in payload
            assert view["ranking"] == []
            assert view["placement"] is None
            for entry in view["finalists"]:
                assert set(entry) == {"teamId", "teamName", "startupName", "tagline"}
            assert '"rank"' not in payload
            assert '"inCount"' not in payload
            assert '"total"' not in payload


def test_projector_and_vote_payloads_do_not_carry_any_pitch_text() -> None:
    teams = _teams_with_feedback()
    pitch_ids = [teams[0].id, teams[1].id, teams[2].id]

    for phase in ("leaderboard", "finale", "voting"):
        state = make_state(teams, phase, pitch_order_team_ids=pitch_ids)
        for payload in (_screen(state), vote_state_view(state, voted_team_id=None)):
            text = json.dumps(payload)
            assert "Problem text of team" not in text
            assert "One change of team" not in text
            assert "Question to team" not in text
