"""Jury ranking and tie-breaks."""

from __future__ import annotations

from dataclasses import dataclass

from .personas import INVESTOR_IDS, InvestorId
from .state import SessionState, Team, is_at_least

MAX_SAFE_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True)
class RankedTeam:
    team: Team
    total: int
    in_count: int
    scores: dict[InvestorId, int]
    submitted_at: int | None


def verdict_is_complete(team: Team) -> bool:
    return all(
        team.verdict.get(investor_id) is not None and team.verdict[investor_id].status == "done"
        for investor_id in INVESTOR_IDS
    )


def round1_is_complete(team: Team) -> bool:
    return all(
        team.round1.get(investor_id) is not None and team.round1[investor_id].status == "done"
        for investor_id in INVESTOR_IDS
    )


def round1_has_failure(team: Team) -> bool:
    return any(
        team.round1.get(investor_id) is not None and team.round1[investor_id].status == "failed"
        for investor_id in INVESTOR_IDS
    )


def verdict_has_failure(team: Team) -> bool:
    return any(
        team.verdict.get(investor_id) is not None and team.verdict[investor_id].status == "failed"
        for investor_id in INVESTOR_IDS
    )


def round1_started(team: Team) -> bool:
    """True once any round 1 call has been requested, so the pitch was already judged."""
    return any(entry.status != "pending" for entry in team.round1.values())


def verdict_started(team: Team) -> bool:
    """True once any final verdict call has been requested, so the answers were already read."""
    return any(entry.status != "pending" for entry in team.verdict.values())


def ranked_teams(teams: list[Team]) -> list[RankedTeam]:
    ranked: list[RankedTeam] = []
    for team in teams:
        if team.eligible is not True or not verdict_is_complete(team):
            continue
        scores: dict[InvestorId, int] = {}
        for investor_id in INVESTOR_IDS:
            entry = team.verdict[investor_id]
            scores[investor_id] = entry.score or 0
        ranked.append(
            RankedTeam(
                team=team,
                scores=scores,
                total=sum(scores.values()),
                in_count=sum(
                    1 for investor_id in INVESTOR_IDS if team.verdict[investor_id].decision == "in"
                ),
                submitted_at=team.submitted_at,
            )
        )
    ranked.sort(key=_sort_key)
    return ranked


def _sort_key(entry: RankedTeam) -> tuple[int, int, int]:
    """One order everywhere: deals first, points break ties, then submission time."""
    return (
        -entry.in_count,
        -entry.total,
        entry.submitted_at if entry.submitted_at is not None else MAX_SAFE_INTEGER,
    )


def ranked_teams_for_state(state: SessionState) -> list[RankedTeam]:
    ranked = ranked_teams(state.teams)
    if not is_at_least(state.phase, "leaderboard") or state.leaderboard_team_ids is None:
        return ranked

    by_team_id = {entry.team.id: entry for entry in ranked}
    frozen: list[RankedTeam] = []
    for team_id in state.leaderboard_team_ids:
        entry = by_team_id.get(team_id)
        if entry is not None:
            frozen.append(entry)
    return frozen


def select_finalists(ranked: list[RankedTeam], count: int = 3) -> list[RankedTeam]:
    """The top ``count`` teams of the one ranking.

    The ranking already puts teams with more "I'm in" decisions first, so the
    finalists always include every funded team before any team without one. If
    fewer than ``count`` teams have an "I'm in", the same ranking fills the
    remaining places, so the finale always has ``count`` teams when that many
    complete teams exist.
    """
    return ranked[:count]


def finalists(teams: list[Team], count: int = 3) -> list[RankedTeam]:
    return select_finalists(ranked_teams(teams), count)
